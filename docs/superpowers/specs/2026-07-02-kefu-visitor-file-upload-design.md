# 智能客服访客文件上传 — 设计文档

- 状态：已通过 brainstorming 评审，待实现计划
- 日期：2026-07-02
- 依赖：智能客服访客页 Stage ②（`KefuChatPage.vue`、`kefuPublic.ts`）+ 公开上传端点（`kefu_public.py`）+ MCP `read_upload`（在 kefu 只读白名单内）

## 1. 背景与目标

让访客在客服对话框里上传文件，Agent 能读取并分析（"文件分析"是智能客服设计的原始能力之一）。**后端与 Agent 读取能力已全部就绪**，本特性仅补齐**访客页前端接线**：

- 公开上传端点 `POST /kefu/sessions/<sid>/files` 已存在（校验 20MB / 扩展名白名单 / 路径安全，存到会话 `uploads/`，返回 `{name,path,size}`）。
- 发消息端点已接收 `attachments`（rel 路径数组），存为 file 部件并向 prompt 注入路径提示 `[用户上传的文件 X，路径：uploads/X（可用工具读取）]`。
- Agent 经 MCP `read_upload`（在 `KEFU_TOOL_ALLOWLIST` 内）读取上传文件——已可用。

**结论：纯前端增量，不改后端。**

## 2. 锁定的关键决策

| 维度 | 决策 |
|------|------|
| 每条消息附件数 | **多个**（send_message 已支持 attachments 数组） |
| 触发 | 输入区「📎」按钮，选文件即上传；上传成功后在输入框上方以可删 chip 列出待发送附件 |
| 发送 | 允许仅附件无文字（后端 `content or attachments` 已允许）；发送时把 attachments(rel 路径) 随 `sendKefuMessage` 一起发 |
| 校验 | 复用后端校验（413 超限 / 415 类型），前端弹错误提示 |
| 消息气泡 | 用户消息渲染文字 + 文件名 chip（`content` 里 `{type:'file',name,path}` 部件） |
| Agent 读取 | 不改——现有路径提示 + `read_upload` 已让 Agent 读取 |
| 范围 | 仅访客页；管理端无关 |

### 明确不做
- 上传进度条（小文件够用，YAGNI）。
- 图片内联预览（先渲染文件名 chip；后续可加）。
- 后端改动（已就绪）。

## 3. 前端改动

### 3.1 `src/api/kefuPublic.ts`
- 新增 `uploadKefuFile(sid: string, file: File): Promise<{name:string;path:string;size:number}>`——`FormData` 打到 `/api/kefu/sessions/${sid}/files`，带 `X-Visitor-Id` 头（用 `@/utils/request` 的 `post` 传 FormData + headers，或原生 fetch）。
- `sendKefuMessage(sid, content, attachments: string[] = [])`——body 加 `attachments`（rel 路径数组）。默认空数组保持既有调用兼容。

### 3.2 `src/views/kefu/KefuChatPage.vue`
- 状态：`pending = ref<{name:string;path:string}[]>([])`。
- 输入区加「📎」按钮：选文件（`<input type="file" multiple>` 或 `el-upload` 手动模式）→ 对每个文件调 `uploadKefuFile` → 成功推入 `pending`（存 name+path）；失败 `ElMessage.error`（展示后端 413/415 消息）。
- 待发送区：`pending` 的 chip 列表（文件名 + ✕ 删除）。
- `send()`：`attachments = pending.map(p=>p.path)`；允许 `draft` 为空但有 `pending` 时可发（放开当前 `!draft.trim()` 的禁用条件为 `!draft.trim() && pending.length===0`）；乐观气泡的 `content` 带上 file 部件；发送后清空 `pending`。
- 消息渲染：用户气泡除 `plainText` 文本外，渲染 `content` 中 `type==='file'` 部件为文件名 chip（新增 `fileParts(content)` helper）。

## 4. 安全 / 边界
- 上传/发送复用后端已有校验（大小/类型/路径穿越 safe_resolve）+ 归属（visitor_id）。前端仅做体验层提示，不放松后端。
- Agent 只读白名单已含 `read_upload`（读会话自身 `uploads/`，会话级隔离），不扩权。

## 5. 测试与验收
- **前端 Vitest**：`uploadKefuFile` 发 FormData 到正确 URL、带 visitor 头；`sendKefuMessage` 透传 attachments；`send()` 仅附件也可发、发送后清 pending；用户气泡渲染文件名 chip。
- **Playwright E2E（必做）**：匿名进入 `/kefu/:slug` → 点 📎 上传一个文本文件（含可识别内容）→ chip 出现 → 发送（可带一句"帮我看看这个文件"）→ Agent 回复体现读到了文件内容；DB/工作区交叉核对 `uploads/` 下有该文件。截图存 `.playwright-mcp/`。
- **文档同步**：更新 `docs/user-guide/ai/smart-customer-service.md`（访客可上传文件、Agent 可读；类型/大小限制）。

## 6. 已知约束
- 上传是逐文件即时上传（选中即传），发送时只带已成功上传的 rel 路径——避免发送时批量上传的复杂度。
- 复用现有「idle→重载历史」对话模型，附件消息同样在 idle 后由历史重载呈现最终气泡。
