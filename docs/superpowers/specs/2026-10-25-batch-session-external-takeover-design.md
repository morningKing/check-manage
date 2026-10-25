# 批任务会话对外接管页 设计

- 日期：2026-10-25
- 状态：待评审（设计默认值已按推荐项选定，见 §10 待确认问题）
- 主诉：外部系统通过 Open API 启动批任务后，有时需要人工接管某个批任务会话。
  希望把批任务会话的对话页对外开放，但**仅暴露一个会话页 + 交互输入框 + 历史
  消息**，不暴露系统任何其它功能。

## 1. 背景（现状事实）

- 外部系统持 API Key（`X-API-Key`，绑定系统用户）调用 `POST /api/v1/ai-batches`
  创建批任务（`server/routes/open_api_batches.py:358`）。一个批任务 = N 个子会话
  （`ai_chat_sessions.batch_id/batch_seq`），对外端点已有 detail/pause/resume/
  cancel/子会话消息读取等，全部按「密钥属主 + api_key_id」双重隔离。
- 对话页现状：管理员的 `BatchConversationView` 刻意只读无输入框；本人会话页对
  **运行中**批子会话发消息被 409（`BATCH_SESSION_CONTROLLED`，`ai_chat.py:863`）；
  **终态**子会话可走 `continue` 通道续聊（`ai_chat_batches.py:572`，worker 串行
  消费，有活跃回合时 CAS 409）。
- **子会话级 pause/resume 已存在**（`resume_child` 等，`ai_chat_batches.py:548`），
  暂停的子任务被 worker 跳过，不影响批内其它子任务推进。
- 系统唯一对外免登录先例：kefu 访客页 `/kefu/:slug`（`meta.public` 无壳路由）+
  `kefu_public.py` 无 JWT 蓝图 + 三元组归属校验 + 双层限速 + SSE 查询参数鉴权
  （`?access_token=` 先例，`auth.py:63`）。
- 后端鉴权为逐视图装饰器制，无全局白名单；未挂装饰器的路由即公开路由。

## 2. 非目标（明确不做）

- 不暴露任何管理端点、菜单、其它会话/批任务的数据——公开蓝图与既有路由物理隔离。
- 页面不提供 pause/resume/cancel/retry 等任务控制按钮——任务控制权仍归外部系统
  的 API 编排。
- 不支持访客创建会话、上传文件、修改批任务配置或 agent/model。
- 不引入账号/登录体系——凭证是短时**接管令牌**（见 D1）。
- 不支持「向运行中回合插话」——worker 对子会话独家驱动，这是既有安全边界。

## 3. 关键决策

### D1 凭证 = 接管令牌签名链接（默认）

外部系统用 API Key 调新端点
`POST /api/v1/ai-batches/<batch_id>/sessions/<session_id>/takeover-link`
为某个子会话签发短时接管令牌，响应返回 `{url, expiresAt}`，外部系统把 URL
转交人工（IM/邮件均可）。人打开即进入会话页。

- 令牌：随机 32 字节，服务端只存 SHA-256 哈希；可主动吊销；刷新页面不失效（令牌非一次性，支撑多轮接管对话）。
- **TTL 部署级可配置**（2026-10-25 增补）：`TAKEOVER_TTL_DEFAULT_HOURS`（请求未带
  `ttlHours` 时的默认，内置 24）与 `TAKEOVER_TTL_MAX_HOURS`（上限，内置 168）两个
  环境变量，调用时读取（改 env 重启后端生效）；1h 为硬下限防误配，默认值恒钳入
  `[1, max]`，上限另有 1 年绝对天花板护栏。请求级 `ttlHours` 优先于部署默认。
- **否决的备选**：
  - 页面粘贴 API Key——长期机器凭证进浏览器（localStorage/内存暴露、误转发
    即失控），否决；
  - kefu visitor_id 模式——visitor_id 由访客自生成、可冒充，是匿名标识不是
    授权凭证，与「人工接管」的授权语义不匹配，否决。

### D2 粒度 = 单个子会话（默认）

令牌绑定三元组 `{batch_id, session_id, api_key_id}`，页面只展示这一个子会话的
历史与输入框。接管出错任务就为它签一个链接。

- 备选（批级令牌 + 子会话列表）暴露面更大，与「仅一个会话页」的主诉不符；
  如后续需要，在同一令牌表上加 scope 即可扩展，本期不做。

### D3 接管语义 = 走既有 continue 通道，准入扩展到「子会话已暂停」（默认）

- 接管页发消息的准入条件：子会话处于**终态**（completed/failed/cancelled，
  现状已支持）或 **child-paused**（唯一的行为扩展点：`continue_child` 的状态
  校验放宽到 paused）。
- 工作流：外部系统发现任务需要人工介入 → 调 API 暂停该子会话（或整批）→
  签发接管链接交人 → 人在页面对话（worker 串行消费，AI 在原 OpenCode 会话
  续跑一轮）→ **paused 子会话对话完成后回到 paused**（不改变外部系统的暂停
  意图），外部系统决定 resume 或终止；终态子会话对话完成后仍为终态。
- 页面发消息遇到 running 子会话：明确报错「任务执行中，请先通过 API 暂停」，
  **不做隐藏的自动暂停副作用**（可选环境开关 `AI_TAKEOVER_AUTOPAUSE=1` 提供
  自动 pause_child，默认关）。
- 消息落库复用 `continue_prompt` 既有机制，人工消息与回复同现在会话历史，
  内部管理员视图（BatchConversationView）天然可见。

## 4. 架构

```
外部系统 ──X-API-Key──▶ 开放 API（/api/v1/ai-batches/...）
   │  ① 签发接管链接 POST .../takeover-link        （api_key_required + 属主校验）
   │  ② 暂停子会话 POST .../sessions/<sid>/pause    （既有端点）
   ▼
人工 ◀──链接 URL────────┘
   │
   └─浏览器─?t=<token>─▶ 对外公开页 /takeover/session（meta.public 无壳）
                │ t=<token>
                ▼
          对外公开蓝图 /external/takeover/*（无 JWT，令牌三元组校验+限速）
                │ 复用
                ├─ batch_repo 消息查询（对外裁剪列）
                ├─ continue_child 通道（worker 串行消费）
                └─ 子会话 SSE 事件流
```

## 5. 后端设计

- **新公开蓝图** `server/routes/external_takeover.py`，前缀 `/external/takeover`
  （无 JWT 装饰器，对齐 `kefu_public.py` 模式）：
  - `GET /external/takeover/session?t=`——令牌校验 → 会话档案（批任务名、
    batch_seq、输入文件名、子会话状态、令牌过期时间）；
  - `GET /external/takeover/session/messages?t=`——历史消息（复用批子会话消息
    查询，**对外裁剪**：仅 role/content parts/created_at，不含 agent、工具调用
    参数等内部字段，口径对齐既有对外子会话 messages 端点的裁剪）；
  - `POST /external/takeover/session/messages?t=`——发消息（D3 状态校验 +
    continue 语义；prompt ≤ 20000 字符，与开放 API 一致）；
  - ~~`GET /external/takeover/session/events?t=`（2026-10-25 修订取消）~~：
    批子会话由 worker 独家写入、回合结束才整体落库
    （`batch_engine._persist_conversation`），流式传输对该数据流无增益；内部
    会话 SSE 与 OpenCode 事件代理/持久化归属深度耦合（`ai_chat.py:1447`），
    不值得为新鉴权面复制。实时性改为前端 3s 轮询 messages+status。
- **新表** `batch_takeover_tokens`：`id, api_key_id, batch_id, session_id,
  owner_user_id, token_hash, expires_at, revoked_at, created_at, last_used_at`。
  迁移按项目规约**双注册**（`migrations/` + `app.py` 显式注册表块）。
- **签发/吊销端点**挂 `open_api_batches.py`：`api_key_required + require_bound_key`
  + 属主与 api_key_id 双重校验（同既有端点口径）。签发响应返回
  `{token, path, expiresAt}`——**不返回拼好的绝对 URL**：服务端不知道自己的
  对外可达域名（反代/内网部署各异），由外部系统用自身已知 origin 拼
  `{origin}{path}?t={token}` 后转交人工。
- **限速**：对齐 kefu 双层限速（IP + 令牌）与 SSE 并发上限。
- **审计**：签发/吊销/接管发消息均 `log_operation` 进 oplog（补上操作日志
  覆盖清单的口径）。

## 6. 前端设计

- 新公开路由 `/takeover/session?t=<token>`（`meta.public`，顶层无壳，对齐
  `/kefu/:slug`）。
- 轻量独立页 `src/views/external/TakeoverSessionPage.vue`：
  - 顶栏：任务名 + batch_seq/输入文件 + 状态徽标 + 令牌过期时间；
  - 消息列表：assistant 内容复用 `MarkdownView`（mermaid/katex/echarts/svg/
    plantuml 渲染全部继承，零额外工作）；用户消息简单气泡；
  - 输入框：Enter 发送；running 态给出「请外部系统先暂停」的结构化错误提示；
  - 实时性：3s 轮询 messages+childStatus（页面隐藏时暂停，visibilitychange）；
    发送后立即触发一次拉取。（2026-10-25 修订：弃 SSE，理由见 §5）
- 不引入主布局、菜单、登录 store；令牌只存在于 URL 与内存，**不写
  localStorage**（共享电脑风险）。

## 7. 安全分析

- 令牌：仅存哈希、TTL、可吊销、绑定三元组；URL 即凭证的泄露风险以短时 +
  可吊销缓解；公开端点响应加 `Cache-Control: no-store`。
- 越权：所有公开端点以令牌内三元组取数，**没有任何用户可控的 id 入参**；
  公开路由唯一，评审清单 = 未挂 JWT 的蓝图仅 `/auth/login`、`kefu_public`、
  `external_takeover` 三处。
- 注入/XSS：消息渲染走 MarkdownView 既有管线（md-editor 安全配置 + img
  XSS 天然安全）；prompt 长度上限与开放 API 对齐。
- 滥用：双层限速 + SSE 并发上限（参数对齐 kefu）。

## 8. 测试策略（项目惯例：真实链路）

- 后端 pytest：令牌签发/过期/吊销/哈希存证、三元组越权拒绝、D3 状态机
  （终态/paused 放行、running 409）、限速、oplog 留痕。
- 前端 vitest：页面组件（历史渲染、发送、SSE stub、令牌缺失/过期 UI）。
- Playwright E2E（真实栈）：API 签发链接 → 公开页打开 → 种子历史消息渲染 →
  暂停后接管发消息 → worker 消费续跑 → SSE 收到回复 → resume；负例：过期
  令牌 404、篡改令牌、页面不可见其它会话。
- 安全：Mimosa 全量扫描基线比对（新增公开端点必须过检）。

## 9. 分期

- **一期**：D1+D2+D3 全量（令牌表/签发吊销/公开蓝图/公开页/continue 扩展
  paused）。唯一核心行为变更是 `continue_child` 准入放宽到 paused 子会话。
- **二期（可选，均不做进一期）**：`AI_TAKEOVER_AUTOPAUSE` 自动暂停开关、
  批级令牌（子会话列表视图）、页顶任务进度只读条。

## 10. 待确认问题（评审时拍板）

1. **D3 工作流**：默认「外部系统先 API 暂停 → 人接管对话 → 外部系统恢复」，
   页面不隐藏副作用——是否符合你们的接管工作流？还是要页面自动暂停（默认开）？
2. **令牌 TTL**：默认 24h / 上限 7 天 / 非一次性，是否合适？是否需要「打开
   即焚」的一次性模式？
3. **粒度**：如需一个链接看整批（子会话列表 + 逐个进入），请指出，当前按
   单子会话设计。
