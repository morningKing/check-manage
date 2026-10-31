# 普通会话 bypass 模式设计（权限询问自动放行）

- 日期：2026-10-31
- 来源：用户需求「普通会话需要有 bypass 模式」；对标 Claude Code bypassPermissions
- 状态：设计待批准

## 0. 范围与决策记录

| 决策点 | 结论 |
|--------|------|
| bypass 范围 | **只自动放行 OpenCode 权限询问**（PermissionCard 不再弹，自动回 `once`）；动作门禁（pre/checkpoint/post）完全不动 |
| 开关粒度 | **会话级**：`ai_chat_sessions.permission_mode`（`'ask'` 默认 / `'bypass'`），重开会话保留；**新会话一律 `ask`**（安全默认，用户确认维持现状） |
| 谁能开 | **developer 及以上**（`@write_required` 天然 403 guest）；前端开关对 guest 隐藏 |
| 应答机制 | **服务端自动应答**（监听器内），非前端自动点卡——标签页关闭回合照跑（批任务 `_reject_pending_permissions` 的镜像先例） |

**明确不做**：动作门禁旁路；批任务会话 bypass（其已有自动拒绝链，不混）；oc 侧 opencode.json permission 配置级放行（需处理重载语义，v1 用应答级）；持久化审计表（v1 结构化日志够用，将来可升级）。

## 1. 核心机制

权限询问事件（oc `permission.asked`，经 `chat_persist._run_listener` 的**全量事件循环**——`chat_persist.py:586`，该线程独立于前端标签页存在）到达时：

1. 查父会话 `permission_mode`（listener 启动时的 `sid` 查库；**子代理的询问按父会话模式判定**——归属逻辑与 `reply_permission` 的 `_owned` 一致：`sessionID` 等于父会话 oc id 或其 kids）。
2. `bypass` → 立即 `OpenCodeClient.reply_permission(request_id, 'once', directory=...)` + 结构化日志落 `ai-chat.log`（session、request_id、permission type、动作=auto-once）。
3. 前端本来就会经自己的 SSE 代理（`ai_chat.py:1783 sse_events`）收到同名 `permission.asked` 帧——**前端按会话模式分流**：`bypass` 时不渲染 PermissionCard，改为在输入区上方渲染「⚡ 已自动放行 <type>」瞬时 chip（store 收集，下回合开始或用户点击后消散）；`ask` 模式行为完全不变。

**模式翻转语义**：切到 bypass 时，切换端点**立即自动放行当前全部挂起询问**（防御性，覆盖监听器启动前滞留的），前端 store 清空 pending 权限卡状态；切回 ask 后新询问照常弹卡。正在自动应答与用户手动应答的竞态由 oc serve 的 request_id 一次性语义吸收（重复 reply 对已决议请求无效报错，吞掉即可）。

## 2. 落点

### 2.1 迁移（三处落点照惯例）

```sql
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS permission_mode VARCHAR(10) DEFAULT 'ask';
```

`server/db_schema/core.py`（ai_chat_sessions 建表块幂等 ADD）+ `server/migrations/2026_10_31_session_permission_mode.py` + `server/app.py` 注册块（exec_module 不可省）。

### 2.2 切换端点

`PATCH /ai/chat/sessions/<sid>/permission-mode`，body `{"mode": "ask" | "bypass"}`：

- `@write_required`（guest 403）+ `_load_session_for_user` 属主校验；非法值 400。
- 批任务受控会话（`sess` 行 `batch_id` 非空的 active 批）拒绝切换，409（批任务有自己的权限处置链）。
- 写库后若 `mode='bypass'`：拉 `client.list_permissions(directory)`，对本会话+kids 的挂起逐个 `reply_permission('once')` 并记日志。
- 响应 `{"sessionId", "mode"}`。

### 2.3 监听器钩子（`chat_persist.py`）

`_run_listener` 事件循环内新增 `_maybe_auto_approve(evt, ...)`（放在 `_audit_capture` 旁）：

- 命中 `permission.asked` 时**查库读该会话当前 `permission_mode`**（权限询问稀有，每次事件一次索引查可忽略；保证中途翻转对后续新询问即时生效，无缓存失同步问题），`'bypass'` 才自动应答。
- `sessionID` 归属判定复用 `_owned` 同款（父 oc id 或 kids）；listener 已知 `known_subtasks`/子会话注册表，判定数据源一致。
- client 从 `_listener_thread` 既有参数/工厂获取，不新建第二条 oc 连接方式。

### 2.4 前端

- **载荷**：`list_sessions`（`ai_chat.py` ~:580）与 openSession 消费的会话载荷加 `permissionMode` 字段（camelCase 映射同现有列风格）。
- **开关**：聊天头部模式切换钮（shield 系图标，`ask`=普通 / `bypass`=高亮警示色，tooltip 说明「自动放行工具权限询问」）；guest 不可见；批任务受控会话（`batchControlled`）禁用。点击即调 2.2 端点，乐观更新 store。
- **PermissionCard 分流**：store 的 `permission.asked` 处理（`aiChat.ts:1377`）按 `permissionMode` 分流——`bypass` 时不入 pending 卡列表，改为推入 `autoApproved` 瞬时列表（type+时间戳）；AiChatView 在输入区上方渲染 chip（复用现有轻量提示区样式），点击或下个 user 消息发出时清空。
- **翻转清卡**：store 的 toggle action 成功且 mode 变 `bypass` 时清空 pending 卡与 `pendingPermission` 状态。

### 2.5 审计

自动放行逐条 `logger.info('permission auto-approved (bypass) session=%s request=%s type=%s', ...)` 落 `ai-chat.log`（日志查看器已可直接检索）；持久化审计表不做（见 §0）。

## 3. 测试

- **pytest**：
  - 迁移三处落点幂等 + 列默认值 `'ask'`（照 2026_10_30 反馈表迁移测试模式）。
  - 切换端点：guest 403 / 非属主 404 / 非法值 400 / 批任务受控 409 / 正常翻转 200 且 list_permissions 挂起被放行（stub client 断言 reply 调用）。
  - 监听器钩子：`permission.asked` × bypass → reply('once') 调用一次；× ask → 不调用；子代理 sessionID → 按父会话模式判定。
- **E2E（真实链路）**：复用 `ai-permission-ask.spec.ts` 的触发先例（读 `C:\Windows\...` 必触发 `external_directory=ask`）——开 bypass → 同款提示词 → **断言权限卡不出现、回合正常完成、「已自动放行」chip 出现**；ask 模式侧由既有 spec 锁死，不重复。
- **回归**：`ai-permission-ask.spec.ts` 全绿（ask 行为未被扰动）；批任务权限自动拒绝链相关用例不回归。

## 4. 风险与开放点

| 风险 | 处置 |
|------|------|
| 自动放行被滥用（developer 开了就 forget） | 会话级隔离（每会话独立工作区）限爆炸半径；chip 常显提醒；日志可审计；模式不跨会话继承 |
| 翻转瞬间的事件竞态（卡已弹、请求已人答、监听器又 auto-reply） | oc 对已决议 request 的重复 reply 幂等/报错，钩子吞错 |
| `permission.asked` 事件形状（properties 字段名）版本漂移 | 钩子按 `reply_permission` 既有消费形状取字段，缺字段则跳过 auto-reply 落 warning（fail-open 到手动卡，不 fail-closed 卡死回合） |
| 翻转与新询问的窗口竞态（翻转写库与事件查库间微秒级交错） | 单行 UPDATE 原子性足够；最坏一次多余/缺失自动应答，下一询问自愈 |
