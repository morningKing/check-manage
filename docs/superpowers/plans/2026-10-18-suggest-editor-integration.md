# suggest-editor 整合进 check-manage（智能写作）

日期：2026-10-18
来源：`C:\Users\admin\.zcode\workspace\default\suggest-editor`（独立原型：Tiptap + Yjs 协作编辑器，
Hocuspocus WebSocket 服务端 + ACP 驱动 opencode/mock agent 产出写作建议）

## 目标

把原型整合为本平台的一个功能域「智能写作」，并补齐三个产品能力：

1. **文档保存 + 文档列表管理** —— 文档元数据入 PostgreSQL，侧边栏菜单进入，列表页管理（重命名/删除/排序）。
2. **模板功能** —— 预置内置模板（迁移 seed），新建文档时选模板；管理员可增删改模板、把当前文档另存为模板。
3. **工作状态持久化（工作区恢复）** —— 正文 + 建议（suggestions map）+ 词表（vocabulary map）+ 已分析块 hash
   全部随 Y.Doc 持久化到数据库，重启/重连后原样恢复；未完成的改写命令在重载后自动续跑；
   agent 工作区（document.md 快照 / 词表.txt）落盘保留。

## 架构

```
浏览器 Vue3 页面（Tiptap @tiptap/vue-3 + Y.Doc + @hocuspocus/provider）
   │  WS: /suggest-ws（vite dev 代理 / proxy.py 生产隧道 → Node :1234）
   │  HTTP: /api/suggest-editor/*（axios → Flask :3002）
   ▼
Flask（元数据权威 + 内部状态仓库）
   │  routes/suggest_editor.py
   │    - 用户端：文档 CRUD / 模板 CRUD(admin) / open
   │    - 内部端（X-Internal-Token = MCP_INTERNAL_TOKEN，同 MCP 内部通道先例）：
   │        GET  /suggest-editor/internal/documents/<id>/access   → 存在性+归属校验
   │        GET  /suggest-editor/internal/documents/<id>/state    → Yjs 二进制(base64) + analyzedHashes
   │        PUT  /suggest-editor/internal/documents/<id>/state    → 落库 + bump updated_at
   ▼
suggest-editor-server/（Node，仓库顶层新目录，同 mcp-server 惯例）
   │  Hocuspocus v4（原 server/src 原样移植，三处改造）：
   │    ① storage.ts：文件落盘 → Flask 内部 API（base64 Yjs update + hashes）
   │    ② index.ts：+onAuthenticate（Node 侧 HS256 校验 JWT，调 access 端点，
   │       guest 置 connection.readOnly）
   │    ③ engine.ts：+resumePendingRewrites（重载后把 pending 改写命令续跑）
   │  agent 不变：mock-agent / `opencode acp`（AGENT_KIND），工作区在 <dataDir>/ws/<docId>/
```

数据表（迁移 `2026_10_18_suggest_editor_tables.py`，**同时**注册进 app.py 启动块）：

- `se_documents`：id(UUID PK) / title / owner_user_id / template_id / status(active|deleted)
  / created_at / updated_at / last_opened_at
- `se_document_states`：document_id(PK) / state(BYTEA，Y.encodeStateAsUpdate) / analyzed_hashes(JSONB) / updated_at
- `se_templates`：id(TEXT PK) / name / description / content_html(TEXT) / is_builtin / sort_order / created_at / updated_at

模板内容格式 = Tiptap HTML（StarterKit+TaskList 覆盖的节点）。应用模板在**客户端**做：
编辑器首次 sync 后若正文为空且文档带 template_id → `setContent(template.contentHtml)`（同一 Yjs 事务），
避免服务端引入 prosemirror HTML→Yjs 转换。

## 关键决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 编辑器前端 | React→Vue3 移植 | React 依赖仅 CollabEditor/BubbleToolbar 两个 UI 文件（~570 行）；核心 1750 行（suggestions/completion/slash-menu/vocabulary/commands/local-rules）全是框架无关 TS，原样拷贝。保持单代码库、统一 JWT/权限/样式 |
| Yjs 状态存储 | PostgreSQL BYTEA（经 Flask 内部端点） | 备份体系覆盖（BACKUP_TABLES）；Node 不直连 DB，Flask 保持唯一 DB 写入方；接口与原 storage.ts 同形 |
| WS 鉴权 | query token 不适用 → provider `token` 字段（Hocuspocus 握手消息携带），Node 用共享 JWT_SECRET 自行 HS256 验签（node:crypto 手写 ~30 行，不引依赖） | WS 握手无法带 Authorization 头；参照 login_required_sse 的 ?access_token= 先例，但 Hocuspocus 原生支持 token 字段更干净 |
| 生产 WS 转发 | proxy.py 新增裸 socket 双向隧道（路径 `/suggest-ws`） | proxy.py 无任何 WS 支持；ThreadingHTTPServer 一连接一线程，隧道 ~100 行可控 |
| 文档删除 | 硬删 documents+states 行 + rmtree agent 工作区 + operation_logs 留痕 | 同 ai-chat 会话 delete 语义 |
| 权限 | 文档=登录+write_required（guest 只读，WS 侧 readOnly）；模板管理=新权限 `admin.suggest_editor` | PERMISSION_CATALOG 增一条；模板管理 UI 内嵌在列表页（can() 控制显隐），不动设置中心 |
| 菜单 | 迁移幂等 upsert `menu-suggest-editor`（menu_type='system'，path=/suggest-editor，roles=[admin,developer]） | settings-hub-menu 迁移先例 |

## 改动清单

**新增**
- `suggest-editor-server/`（Node 服务：src/*.ts 移植 + 改造、mock-agent.mjs、package.json、tsconfig）
- `server/migrations/2026_10_18_suggest_editor_tables.py`
- `server/utils/suggest_editor_repo.py`、`server/routes/suggest_editor.py`
- `src/types/suggestEditor.ts`、`src/api/suggestEditor.ts`
- `src/components/suggest-editor/`（core/*.ts 六件套 + SuggestEditorCore.vue + BubbleToolbar.vue + SuggestionSidebar.vue + styles）
- `src/views/suggest/SuggestEditorListView.vue`、`src/views/suggest/SuggestEditorDocView.vue`
- `e2e/suggest-editor.spec.ts`、`server/tests/test_suggest_editor_routes.py`、前端 vitest

**修改**
- `server/app.py`（蓝图注册 + 迁移启动块）
- `server/utils/permissions.py`（权限目录）
- `src/router/index.ts`（两条静态路由）
- `vite.config.ts`（/suggest-ws ws 代理）
- `package.json`（deps + se script + dev:all）
- `server/proxy.py`（WS 隧道 + 托管 Node 子进程）
- `start.sh`（端口清理 1234）
- `server/utils/backup.py`（BACKUP_TABLES 增三表——若该清单存在；否则验证默认全表备份）
- `docs/user-guide/`（新功能文档 + 索引）

## 测试与验收

- pytest：迁移表存在+模板 seed；文档 CRUD 与归属隔离（404 语义）；内部端点 token 门 + state 往返；模板权限。
- vitest：api 层。
- Playwright（真实链路，dev:all 带 Node 服务）：登录 → 侧栏「智能写作」→ 选模板建文档 → 模板内容渲染 →
  输入触发文本 → 本地即时建议出现 → mock agent 建议卡片出现 → **刷新页面正文+建议恢复** → 列表改名/删除。
- proxy.py 生产链路：build 后经 :8080 验证 WS 隧道握手 + 页面可用。

## 边界（v1 不做）

- 多实例部署 / Hocuspocus 横向扩展（单机文件工作区）。
- 词表的前端管理 UI（沿用工作区 词表.txt 文件编辑 + 热更新机制）。
- 建议历史版本、模板版本化。
