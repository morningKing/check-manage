# 智能客服系统 — 设计文档

- 状态：已通过 brainstorming 评审，待实现计划
- 日期：2026-06-30
- 关联：复用「AI Agent Chat (M1)」与「AI Chat — Batch Tasks」基础设施（见 `CLAUDE.md`）

## 1. 背景与目标

基于现有 AI Chat 能力，构建一个**公开、免登录**的智能客服。客服由 OpenCode Agent 驱动、后台可配置，独立于主管理系统（不在管理系统侧边栏出现入口，直接通过链接进入），但可复用主系统的部分能力（尤其是 MCP 查询系统数据）。

### 能力范围（v1）

- **知识问答**：基于 Agent 自身能力/知识库回答通用问题（知识库检索在 Agent 内部实现，不由本系统注入）。
- **白名单系统数据查询**：访客可查询系统中授权可见的数据页，经 MCP 只读访问，受 RBAC 钳制。
- **文件分析**：访客上传文件（图片/文档/表格），Agent 分析并给出结论。
- **转人工 / 人工接管**：访客或 Agent 可升级到真人客服。
- **安全护栏**：公网匿名 + MCP 数据访问的必需防护（始终内建）。

### 明确不做（v1 排除，留 v2）

个人数据查询（不做身份核验）、人机验证、满意度评价 + 分析看板、引用来源、线索/留言收集、多语言自动应答。

## 2. 锁定的关键决策

| 维度 | 决策 |
|------|------|
| 能力 | 知识问答 + 白名单系统数据查询 + 文件分析（**不**查个人数据） |
| 数据边界 | 复用 RBAC，客服跑在专用只读角色下；菜单 `roles` 决定可见数据页 |
| 配置项 | Agent + 模型、系统提示词、欢迎语 + 引导问题、多客服实例 |
| 访客身份 | 浏览器 localStorage 匿名访客 ID，单会话可恢复，后端当作伪身份 |
| 形态 | 独立全页 + 可嵌入 `<script>` 悬浮窗 |
| 防滥用 | 按访客 ID/IP 限速（v1 仅限速；启停作为实例配置已具备） |
| 架构 | 方案 C 混合：复用 `ai_chat_sessions` 表 + 引擎 utils；公开入口独立最小蓝图 |

## 3. 架构总览（方案 C 混合）

```
访客浏览器 (匿名凭证)
   │  独立全页 /kefu/:slug  ┐
   │  悬浮窗 <script> iframe ┘
   ▼
kefu_public_bp  (无 JWT，匿名凭证 + 限速，最小公开攻击面)
   │
   ├── 复用引擎 utils: workspace / opencode_client / chat_persist / session_token / 文件上传
   │
   ▼
OpenCode serve ──(MCP url + token)──▶ MCP server (check-manage)
                                          │  validate_session_token → JOIN users → role
                                          ▼  RBAC 只读钳制 (无需改 MCP)
                                       系统数据 (白名单数据页)

管理员 (admin.kefu / admin.ai_chat_admin)
   ▼
kefu_admin_bp  (实例 CRUD/启停 + 会话队列 + 人工接管)
```

**复用红利**：SSE 流、文件上传、OpenCode/MCP 集成、消息持久化、会话恢复（`_recover_session_and_resend`）、生命周期治理全部复用。**安全隔离**：公开匿名能触达的端点收敛到 `kefu_public_bp` 一个小蓝图，便于审计与加固。

## 4. 数据模型

### 4.1 新表 `kefu_instances`

```sql
CREATE TABLE IF NOT EXISTS kefu_instances (
  id               VARCHAR(100) PRIMARY KEY,
  slug             VARCHAR(100) NOT NULL UNIQUE,   -- URL 标识，如 /kefu/presale
  name             VARCHAR(200) NOT NULL,
  agent            TEXT,                            -- OpenCode agent，空=默认
  model            TEXT,                            -- 空=全局默认
  system_prompt    TEXT,                            -- 实例人设/边界（之上再拼不可覆盖护栏）
  welcome_message  TEXT,
  guided_questions JSONB NOT NULL DEFAULT '[]',     -- 引导问题快捷按钮（点击即发送）
  branding         JSONB NOT NULL DEFAULT '{}',     -- {avatar,title,themeColor}
  bot_user_id      VARCHAR(100) NOT NULL REFERENCES users(id),  -- 决定 MCP 只读角色
  enabled          BOOLEAN NOT NULL DEFAULT true,
  rate_limit       JSONB NOT NULL DEFAULT '{}',     -- {perMinute,perDay}
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.2 `ai_chat_sessions` 增列（幂等 ALTER）

```sql
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS kefu_instance_id VARCHAR(100) REFERENCES kefu_instances(id) ON DELETE SET NULL;
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS visitor_id       VARCHAR(100);
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS needs_human      BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS human_takeover   BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_chat_sess_kefu ON ai_chat_sessions(kefu_instance_id, visitor_id);
```

普通 AI chat 会话这些列为空/默认，行为不变。

### 4.3 系统 bot 用户与角色

- 新增内置只读角色（如 `kefu-guest`）：read-only，无 `admin.*`，可见数据页由各数据页菜单 `roles` 显式授予。
- 每个实例绑定一个系统 bot 用户（`users` 行，role = 该只读角色）。kefu 会话 `user_id` 指向 bot 用户。
- **MCP 零改动**：`mcp-server/auth.py` 的 `validate_session_token` 已 `JOIN users ON user_id` 取 `role`，bot 用户的只读角色自动生效。

## 5. 公开入口 `kefu_public_bp`（无 JWT）

所有端点**无 JWT**，改用「匿名凭证」识别（请求头/查询参 `X-Visitor-Id` 或 `visitor_id`）；每个写端点经限速中间件（按 `visitor_id` + 客户端 IP）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/kefu/i/<slug>` | 实例公开配置：欢迎语、引导问题、branding、`enabled` |
| POST | `/kefu/i/<slug>/sessions` | 用 `visitor_id` 建/复用会话（建工作区、写 opencode.json、签 token、起 OpenCode session，`user_id=bot_user_id`、`kefu_instance_id`、`visitor_id`） |
| GET | `/kefu/sessions/<id>/messages` | 历史（校验 `visitor_id` 归属） |
| POST | `/kefu/sessions/<id>/messages` | 发消息；若 `human_takeover` 则不转发 Agent，仅落库等人工 |
| GET | `/kefu/sessions/<id>/events` | SSE：合并 OpenCode 流 + 人工消息进程内广播 |
| POST | `/kefu/sessions/<id>/files` | 上传（复用现有上传，带类型/大小/数量上限） |

**归属校验**：每个 `/kefu/sessions/<id>/*` 都核对 `session.visitor_id` 与请求凭证一致，防止猜 session id 串话。**实例下线**（`enabled=false`）：写端点返回「客服暂时下线」。

## 6. 安全护栏（始终内建）

1. **角色钳制**：bot 用户只读 + 菜单 `roles` 白名单，MCP 物理上读不到未授权数据页。
2. **系统提示词加固**：在实例 `system_prompt` 之上，后端拼接一段**不可被实例配置覆盖**的边界声明——不得导出全量数据、不得执行越权/写操作、忽略试图改写系统指令的用户输入。
3. **限速**：按 `visitor_id` + IP，超 `rate_limit` 拒绝。
4. **文件上限**：类型/大小/数量（复用现有上传校验默认值）。
5. **公开面收敛**：仅 `kefu_public_bp` 暴露给匿名；内部端点不被复用到公开路径。

## 7. 转人工 / 人工接管

- **触发**：访客点「转人工」按钮，或 Agent 通过特定标记请求升级 → `needs_human=true`，Agent 生成一段对话摘要供人工参考。
- **管理端队列**：归入 `admin.kefu` 权限（见 §8），新增 kefu 会话队列视图，展示「待接管」会话。
- **接管**：管理员点「接管」→ `human_takeover=true`（Agent 暂停转发）；管理员消息写入 `ai_chat_messages`，经**进程内广播**推到访客 SSE（事件类型 `human_message`）。可「释放」交回 Agent（`human_takeover=false`）。
- **前端**：访客页 SSE 同时处理 `assistant`（OpenCode）与 `human_message`（人工）两类事件，统一渲染进消息流。

> **单进程假设**：进程内广播依赖生产为单 waitress 进程（`BACKEND_THREADS` 线程池）。若未来横向扩展为多进程，需替换为 Redis pub/sub —— 已在此标注为已知约束。

## 8. 管理端配置 `kefu_admin_bp`

- 权限：新增能力键 `admin.kefu` 入 `PERMISSION_CATALOG`（`require_permission('admin.kefu')`）。
- 端点：`kefu_instances` 的 CRUD + 启停 + 接管/释放 + 会话队列 + 归档。
- 前端：新增管理页 `src/views/admin/KefuManager.vue`（`/admin/kefu`），仅对持 `admin.kefu` 的角色可见。

## 9. 前端

- **独立全页**：新路由 `/kefu/:slug`，独立 layout（不挂主系统菜单/侧栏/鉴权守卫），复用现有 message list / markdown view / input / 文件上传组件。
- **悬浮窗**：独立构建 entry，产出 `kefu-widget.js`；外部站点贴 `<script src=".../kefu-widget.js" data-slug="xxx"></script>`，渲染右下角气泡 + 点击弹出 iframe（指向独立全页）。
- **匿名凭证**：localStorage `kefu:visitor_id`，无则生成 UUID，所有请求携带。

## 10. 生命周期与审计

- kefu 会话复用现有 close/clear/delete 治理与会话恢复（`_recover_session_and_resend`）。
- 审计写 `operation_logs`，`target_type='kefu_session'` / `'kefu_instance'`：实例创建/启停/编辑、会话接管/释放/归档、限速触发。

## 11. 蓝图注册顺序

`kefu_public_bp`、`kefu_admin_bp` 在 `app.py` 中注册于 `dynamic_bp`（catch-all）**之前**，与其他 AI-chat/admin 蓝图并列，避免被 `/<collection>` 通配遮蔽。

## 12. 测试与验收

- **后端**：`kefu_instances` CRUD、公开端点归属校验、限速、护栏拼接、bot 用户角色钳制（pytest，`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）。
- **前端**：独立全页与 widget 组件单测（Vitest）。
- **Playwright（必做）**：免登录进入 `/kefu/:slug` → 发消息得到流式回复 → 上传文件分析 → 触发转人工 → 管理端接管并回消息 → 访客侧收到人工消息。截图存 `.playwright-mcp/`。
- **文档同步**：新增 `docs/user-guide/ai/` 下客服使用文档并链入 `docs/user-guide/README.md`。

## 13. 已知约束 / 未决

- 进程内广播仅适用单进程部署（见 §7 标注）。
- 悬浮窗跨域嵌入需配置 CORS / iframe 允许来源（实现时确认 `flask-cors` 配置与 `X-Frame-Options`）。
- v2 候选：个人数据查询（含身份核验）、满意度 + 分析看板、引用来源、线索收集、多语言、人机验证。
