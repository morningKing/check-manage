# AI 批任务 · 对外人工接管

批任务的每个子任务可以**签发一条短时接管链接**，发给外部人员（无需平台账号）——对方在浏览器打开链接，即可进入一个**免登录的精简对话页**，查看该子任务的对话历史、在任务暂停/结束后发消息续跑、任务跑偏时一键中断。链接由外部系统通过 **Open API** 签发，令牌短时有效、仅存哈希、可随时吊销。

> 与智能客服的区别：智能客服是**站点级公开对话入口**（无具体任务上下文）；对外接管是**定向到某个批任务子会话**的临时通道，复用该子任务已有的 AI 上下文（见 [智能客服](./smart-customer-service.md)）。

## 链接组装规则

链接分两步产生：**先签发令牌，再把令牌拼进页面路径**。

### 第一步：Open API 签发令牌

```
POST /api/v1/ai-batches/{batch_id}/sessions/{child_id}/takeover-link
```

- 鉴权：`X-API-Key` 请求头（批任务 Open API 同一套密钥，密钥须已绑定用户）。
- `{child_id}` 支持三种写法：**子任务序号**（如 `3`）、**子会话名称**、**会话 id**。
- 可选请求体 `{"ttlHours": N}`——不传则用部署级默认（环境变量 `TAKEOVER_TTL_DEFAULT_HOURS`），上限受 `TAKEOVER_TTL_MAX_HOURS` 约束。

响应（201）：

```json
{
  "token": "tk_3xKq9f2mA7vBwLd0pRtYs8nC1hJe5uGoZiXaMqWbNvE",
  "path": "/takeover/session?t=tk_3xKq9f2mA7vBwLd0pRtYs8nC1hJe5uGoZiXaMqWbNvE",
  "expiresAt": "2026-11-03T12:00:00+00:00"
}
```

令牌格式为 `tk_` + 43 字符随机串；数据库**只存 SHA-256 哈希**，不落明文；令牌绑定「签发密钥所属用户 + 批任务 + 子会话」三元组，换批换人皆不可用。

### 第二步：拼完整 URL

```
{站点根地址}{path}
```

即 **部署站点根地址 + `/takeover/session?t={token}`**。`/takeover/session` 是前端公开路由（免登录），令牌只走 URL 查询参数 `?t=`，不写入 localStorage。

### 样例

开发环境：

```
http://localhost:5173/takeover/session?t=tk_3xKq9f2mA7vBwLd0pRtYs8nC1hJe5uGoZiXaMqWbNvE
```

生产环境（经 `proxy.py` 反代，端口 8080）：

```
http://your-host:8080/takeover/session?t=tk_3xKq9f2mA7vBwLd0pRtYs8nC1hJe5uGoZiXaMqWbNvE
```

> 把链接发给外部人员时请走私密渠道（令牌即凭证，持有者即可发消息与中断任务）；链接过期或被吊销后，页面会显示「链接无效或已过期，请确认链接是否完整，或联系任务发起人重新获取」。

### 吊销

```
POST /api/v1/ai-batches/{batch_id}/sessions/{child_id}/takeover-link/revoke
```

请求体 `{"tokenId": "tkt_xxxxxxxx"}`——`tokenId` 是签发时令牌的内部 id（`tkt_` 前缀，与 `token` 不同）。三元组（本人 + 本批 + 本子会话）全匹配才生效，拿别人的 tokenId 一律 404。吊销后链接立即失效。

## 接管页功能范围

**不是完整的 AI 会话页**，而是面向外部人员的精简对话页（`/takeover/session`）：

| 能力 | 说明 |
|------|------|
| 会话信息头 | 任务标题、批任务名、子任务序号 `#seq`、输入文件名、状态徽标、链接有效期 |
| 对话历史 | 只显示**用户消息与 AI 回复**两类；AI 回复正文按 Markdown 渲染，工具调用等非文本片段折叠为一行提示；**只拉最近 100 条**（超过时返回体带 `truncated` 标记） |
| 实时刷新 | 每 **3 秒**轮询一次，浏览器切到后台标签页时自动暂停 |
| 发送消息 | 文本框 + Enter / 发送按钮；单条上限 **20000 字符**；发消息会续跑**同一个子会话**（AI 上下文保留，不是新开会话） |
| 中断任务 | 子任务运行中（pending/running）时输入框禁用并出现「中断任务」按钮——先点一次武装、再点一次确认的两段式防误触；中断为**协作式**（由后台 Worker 收口），中断后可用纠正消息继续 |
| 失效兜底 | 令牌无效 / 过期 / 被吊销 → 错误卡片，提示联系任务发起人重新获取 |

### 发消息的状态约束

不是任何时候都能发：

- 子任务 **pending / running**（执行中）→ 拒绝（409，提示「任务执行中，请外部系统先暂停后再接管」）；
- 子任务处于 **completed / failed / cancelled / needs_review / paused** → 允许发消息续跑；其中 **paused** 会自动带重新入队语义；
- 其他状态 → 拒绝。

### 相对内部 AI 会话页没有的功能

- 不能切换会话，也看不到同一批任务的**其他子任务**（一条令牌只定向一个子会话）；
- 无 Runtime / 技能 / MCP / 模型选择面板，一切沿用批任务自身配置；
- 无文件、图片上传，无 artifact（产出文件）查看；
- 无动作门禁审批交互（审批仍由任务发起方处理）；
- 无会话大纲导航、历史搜索、导出、PlantUML / 图片区块等富内容交互。

## 安全与限速

- **三层限速**：读（轮询/查信息）60 次/分钟、2000 次/天；发送桶（发消息与中断共用）10 次/分钟、200 次/天；按 IP 兜底 60 次/分钟、1000 次/天。超限返回 429。
- 页面接口响应均带 `no-store`，浏览器不缓存；
- 每次签发、发送、中断都落操作日志（`ai.batch_takeover_link_created` / `ai.batch_takeover_message` / `ai.batch_takeover_cancel`），可在操作日志中审计；
- 令牌过期 / 吊销后即使链接泄露也无法使用；明文令牌不落库（仅哈希）。

## 相关

- 批任务本体见 [AI 批任务](./batch-tasks.md)；对外 API 全貌见 [AI 批任务 API](../integration/ai-batch-api.md)。
- 站点级公开对话入口见 [智能客服](./smart-customer-service.md)。
