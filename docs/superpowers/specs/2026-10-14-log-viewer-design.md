# 运行日志查看器（ai-chat.log / oc-serve.log） — 设计

日期：2026-10-14 ｜ 状态：已评审（brainstorming 定稿，待出实施计划）
集成位置：管理后台 AI 能力组「运行日志」· 可观测性
由来：2026-10-14 门禁生产排查——命中 0/1、token 错位等问题只能上服务器
`grep ai-chat.log / oc-serve.log`，查看门槛高。双端日志代码补齐当天暴露此缺口。

## 1. 背景与目标

AI 运行时的两条关键日志（`server/ai-chat.log`：后端应用日志（门禁命中/403、
会话/批任务链）；`server/oc-serve.log`：OC serve 进程输出（插件 console.error、
baize-trace 事件））目前没有任何界面上可看的出口。生产排障（如"门禁有没有命中、
插件有没有发请求、token 是否错位"）必须在服务器上翻文件。

目标：管理页上实时跟尾查看这两个文件，支持暂停/自动滚动/关键字筛选——
把"日志定性"从 SSH + grep 变成页面上点开就看。

已与需求方对齐的决策：

1. **固定双文件**（chat/serve 枚举切换），不做通用文件选择器；
2. **HTTP 增量轮询**（1.5s）而非 SSE 推流——跟尾本质是"读只会变长的文件"，
   短请求模型无长连接状态、天然容错轮转/断网/浏览器休眠，实现量最小；
3. 功能面 = 实时跟尾 + 暂停/自动滚动 + **关键字筛选**（纯前端子串过滤，
   等价 `grep <关键字>`）；
4. 权限 `admin.ai_chat_admin`（日志含聊天内容与用户提示词，与其他 AI 管理
   读端点同权）；
5. 入口：管理后台 AI 能力组「运行日志」（与「门禁策略」同级）。

## 2. 非目标

- 不做历史回溯（不能翻到跟尾窗口之前的行）——是"跟尾查看器"不是完整阅读器；
- 不提供轮转备份文件（`.1`/`.2`）的查看；
- 不做下载/导出、多文件、跨机（只读本机 backend 所在目录的两个文件）；
- 关键字筛选不开服务端参数（纯前端；服务端只保证增量正确性）；
- 不做日志写入/清理等写操作（只读端点，无操作日志留痕必要）。

## 3. 架构

```
frontend LogViewer.vue ──1.5s 轮询──▶ GET /api/ai/logs/tail?file=chat|serve&offset=&limit=
                                          │  ai_logs.py（鉴权/参数/白名单）
                                          ▼
                                     utils/log_tail.py read_tail()（纯函数，无 Flask 依赖）
                                          ▼
                                     server/ai-chat.log ｜ server/oc-serve.log
```

- **新增** `server/utils/log_tail.py`：跟尾纯函数（单测对象）。
- **新增** `server/routes/ai_logs.py`：蓝图 `/ai/logs`，端点 GET `/tail`；
  文件白名单、参数校验、`@require_permission('admin.ai_chat_admin')`。
- `server/app.py` 注册蓝图（一行）。
- **新增** `src/api/adminLogs.ts`（fetch 封装）+ `src/views/admin/LogViewer.vue`
  （页面）；管理后台 AI 能力组菜单/路由加「运行日志」入口（注册点对齐现有
  admin 菜单配置）。
- 日志路径解析与 `logging_setup.py` 同款：相对 `server/` 目录
  （`os.path.dirname(os.path.dirname(__file__))`），路由层固定映射，**不接受
  用户传入路径**。

## 4. 后端契约

### 4.1 请求

`GET /ai/logs/tail`（前端经 `/api` 前缀代理，普通 fetch 带 Authorization 头——
轮询机制没有 EventSource 的鉴权限制）。

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `file` | enum | 必填 | `chat` → `server/ai-chat.log`；`serve` → `server/oc-serve.log`；其他值 400 |
| `offset` | int | -1 | 字节读位；**-1 = 首次哨兵**（从文件末尾窗口回读最后 N 行，返回真实 offset）；≥0 增量续读 |
| `limit` | int | 300 | 单次最多返回行数，1..500，越界 400 |

### 4.2 响应（200）

```json
{"file": "chat", "offset": 12345, "size": 12345,
 "rotated": false, "truncated": false, "missing": false,
 "lines": ["2026-10-14 ... gate checkpoint-before hit ..."]}
```

### 4.3 语义

- **offset=-1 哨兵**：seek 到 `max(0, size - 256KB)`，读该窗口内的完整行，
  返回**最后 `limit` 行**，`offset=size`（客户端从末尾续读）。窗口内更早的行
  直接舍弃（跟尾语义，不标记）。
- **增量**：seek(offset) 向后读（单次最多 256KB）；**只吐完整行**——最后一个
  换行符之后的内容视为半行留在文件里，`offset` 只推进到最后一个完整行末。
  返回最早未读的 `limit` 行；未到文件尾即触限 → `truncated=true`（下次续读）。
- **轮转/截断容错**：`offset > size` 判定轮转 → `rotated=true`，按哨兵语义
  重新锚定末尾窗口并重读（客户端清缓冲、插分隔线）。
  已知限制：offset ≤ size 的文件中段篡改检测不了（接受，不为此读 hash）。
- **单行上限**：完整行超 4000 字符截断显示（尾缀 ` …[已截断]`），offset 仍
  推进整行——超长提示词不撑爆页面。
- **文件缺失**：`{missing: true, lines: [], offset: 0, size: 0}`；客户端保持
  哨兵 offset 继续轮询，文件出现后按首次加载走。
- **编码**：utf-8 `errors='replace'`。
- **错误**：非法 file/offset/limit → 400；无权限 → 403；读文件异常 → 500 +
  warning 日志（不留痕操作日志）。
- **上限预算**：每次请求最多 256KB 读、500 行、单行 4000 字符——单观看者
  1.5s 一次的成本封顶为"seek + 一次 ≤256KB 读"。

## 5. 前端行为（LogViewer.vue）

- **布局**：工具栏（文件切换 / 关键字筛选 / 暂停·继续 / 自动滚动开关 / 清屏 /
  状态点+行数）+ 等宽字体日志区（深底色，等宽换行）。
- **轮询生命周期**：1.5s `setInterval`；暂停 = 停表（不发请求）；页面
  `visibilitychange` 隐藏自动停、切回立即补一次；网络失败状态点转「连接中断」
  并继续重试（不弹循环 toast）。
- **自动滚动**：默认开；用户手动上滚即自动关（惯例行为），右下角「回到底部」
  按钮恢复。
- **筛选**：大小写不敏感子串匹配，纯渲染层过滤（缓冲不变）；计数显示
  「命中 M / 共 N」。
- **缓冲上限**：最多保留 5000 行，超出丢最早的行并显示「已裁剪更早日志」标记。
- **轮转**：`rotated=true` 清缓冲、插入「文件已轮转，重新加载」分隔线。
- **缺失**：`missing=true` 显示「暂无日志文件（等待产生）」，不当作错误。

## 6. 测试

- **后端单测**（`server/tests/test_log_tail.py` + 路由用例）：增量读不重不漏、
  半行缓冲（写半行→补全）、哨兵尾窗、offset>size 轮转、超长行截断、
  limit/truncated 续读、文件缺失、非法参数 400、未授权 403。
- **前端组件测**（vitest）：渲染与文件切换、暂停真的停表（假定时器）、
  筛选减行与计数、缓冲裁剪。
- **真实链路 E2E**（`e2e/admin-log-viewer.spec.ts`）：向真实 `ai-chat.log`
  追加唯一标记行 → 打开页面断言轮询窗口内出现；暂停后追加 → 断言不出现、
  恢复后出现；筛选关键字 → 断言只显命中行。
  **轮转场景不做 E2E**（Windows 下重命名被占用的日志文件不可靠），由后端
  单测 + 前端组件测（mock rotated 响应）覆盖。

## 7. 风险与边界

- 轮询成本：每观看者峰值 256KB/1.5s（实际多数请求只有几行新增）；管理员
  场景观看者 ≤ 个位数，可忽略。
- 中段截断检测不了（见 §4.3 已知限制）——真实日志只有尾部追加与整文件轮转
  两种形态，够用。
- Windows 文件占用对读无影响（只读打开，不与写侧轮转互斥）。
