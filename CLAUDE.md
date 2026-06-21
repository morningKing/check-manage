# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Development
```bash
# Install dependencies
npm install
pip install flask flask-cors psycopg2-binary PyJWT pytest apscheduler

# Initialize database (runs DDL and seeds default admin)
cd server && python init_db.py

# Start development (both frontend and backend)
npm run dev:all
# Frontend: http://localhost:5173
# Backend: http://localhost:3002

# Run individually
npm run server  # Backend only (port 3002)
npm run dev     # Frontend only (port 5173, proxies /api to :3002)
```

### Testing
```bash
# Frontend (Vitest)
npm run test              # Run all frontend tests
npm run test:watch        # Watch mode

# Backend (Pytest)
npm run test:server       # Run all backend tests

# Both frontend and backend
npm run test:all

# Run single test file
npx vitest run src/stores/__tests__/menu.test.ts
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_backup.py -v
# (PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 is required on Windows — matches `npm run test:server`)
```

### Build
```bash
npm run build  # vue-tsc type check + vite build → dist/
```

## Architecture Overview

This is a **configuration-driven** dynamic data management platform. Unlike traditional CRUD apps, you do **not** create new Vue pages or database tables for new business entities. Instead, you define a `PageConfig` (field schema) and a `Menu` entry, and the system auto-generates the UI and API endpoints.

**Tech stack**: Vue 3 + TypeScript + Element Plus + Pinia (frontend), Python Flask + psycopg2 (backend), PostgreSQL with JSONB (database).

### Key Architectural Patterns

1.  **Single-Table Dynamic Data**: All business data is stored in `dynamic_data` table using PostgreSQL JSONB. The `collection` field (derived from `pageId`) separates entities. This eliminates the need for schema migrations when adding new entities or fields.

2.  **Frontend Dynamic Rendering**: `src/views/dynamic/DynamicPage.vue` is the only page for business data. It reads `pageId` from the route, fetches the `PageConfig` schema, and renders three view modes based on field definitions:
    *   **Table view** (default): `DataTable` + `DynamicForm` dialog
    *   **Kanban view**: `KanbanBoard` with configurable group/card fields
    *   **Excel view**: `ExcelView` using Univer.js spreadsheet

3.  **Dynamic Route Generation**: `src/router/dynamicRoutes.ts` reads the `menus` table at runtime and generates Vue Router routes pointing to `DynamicPage.vue`.

4.  **Field-Driven Logic**: Behavior is determined by `FieldConfig` in `page_configs.fields`. For example:
    *   `controlType: 'relation'` triggers M:N relationship handling via `data_relations` table.
    *   `controlType: 'autoSequence'` triggers sequence generation logic in the store.
    *   `controlType: 'reference'` creates a 1:N link and inherits parent fields into child records.
    *   `controlType: 'quoteSelect'` creates a single-direction multi-select reference.

### All Control Types

Defined in `src/types/field.ts`. The `controlType` value determines which component renders the field:

| Type | Description |
|------|-------------|
| `text`, `textarea`, `richText`, `number` | Input controls |
| `markdown` | Markdown editor (md-editor-v3 `MdEditor` with live preview); stored as Markdown source, rendered read-only via `MdPreview` in the detail dialog + plain-text preview in table cells |
| `select`, `multiSelect`, `radio`, `checkbox` | Selection controls |
| `date`, `datetime` | Date/time pickers |
| `file`, `image` | Upload controls |
| `relation` | M:N bidirectional association via `data_relations` table |
| `reference` | 1:N parent-child with field inheritance |
| `quoteSelect` | Single-direction multi-select quote |
| `autoSequence` | Auto-incrementing ID (e.g., "IC-001") |
| `autoTimestamp` | Auto-filled on create/update |
| `compositeText` | Read-only, auto-computed by joining other field values with a configurable separator (`compositeTextConfig.sourceFields`) |

Fields may also carry a `workflowConfig` (transitions, role gating, conditions, and actions) — used to drive status-style fields through a state machine. See `WorkflowConfig` in `src/types/field.ts`.

### Project Versioning System

Project-level branching for isolating work across multiple data pages within a project:

*   **Branch Creation**: `project_versions` table tracks branches with `projectMenuId`. When creating a branch, records from all collections under that project menu are copied with the new `branch_id`.
*   **Branch Locking**: Branches can be locked (`isLocked`, `lockedBy`, `lockedAt`) to prevent modifications during merge preparation.
*   **Merge Flow**: Merge copies branch records to `main` branch, handling conflicts via `MergeStrategy` (`theirs` = branch wins, `ours` = main wins).
*   **Delete Protection**: Branches referenced by cross-project dependencies cannot be deleted (checked via `check_branch_delete_protection`).
*   **Post-Merge Validation**: After merge, dependent project dependencies are re-validated and notifications sent if state changed.

Key files: `server/utils/project_version.py` (merge logic, branch CRUD), `server/routes/project_versions.py` (API), `src/types/version.ts`.

### Cross-Project Dependencies

Three dependency types for coordinating work across projects:

| Type | Behavior |
|------|----------|
| `track-main` | Auto-follow target project's main branch. No merge blocking. |
| `read-write` | Coordinated branch pairing. Merge blocked if target branch not merged. |
| `read-only` | Pinned to specific version. No blocking, version must exist. |

Validation triggers: on dependency creation/update, after target project merge, periodically via scheduler. Notifications (`dependencyBroken`, `dependencyWarning`, `dependencyResolved`) sent to source project admins on state changes.

Key files: `server/utils/cross_project_dependency.py` (validation logic), `server/utils/dependency_scheduler.py` (hourly validation), `server/utils/notifier.py` (notifications), `server/routes/cross_project_dependencies.py` (API).

### Webhook Rules System

Event-driven webhooks with HMAC-SHA256 signature verification:

*   **Trigger Events**: `create`, `update`, `delete`, `merge` on specified collections.
*   **Trigger Conditions**: Optional JSON filter for conditional triggering (e.g., `{"status": "completed"}`).
*   **Execution**: Retry logic with configurable timeout. Logs stored in `webhook_logs`.
*   **Signature**: `X-Webhook-Signature` header contains HMAC-SHA256 of payload with rule's secret.

Key files: `server/utils/webhook_engine.py` (execution, retry, signing), `server/routes/webhooks.py` (API), `src/types/webhook.ts`.

### AI Agent Chat (M1)

A Claude-style chat drawer that connects to an **OpenCode** agent runtime (not a raw LLM). The browser talks only to Flask; Flask is the gateway and SSE proxy, and OpenCode reaches platform capabilities through a **standalone MCP server** (decoupled from Flask).

*   **Flow**: Flask creates a per-session workspace dir + an opaque session token, asks OpenCode (`opencode serve` HTTP API) to start a session bound to that workspace, and registers the MCP url `http://<mcp>/mcp?token=<token>` with OpenCode. The MCP server validates the token by DB lookup (supports revocation) and derives the user/role for RBAC.
*   **Streaming**: `GET /ai/chat/sessions/:id/events` is a `text/event-stream` proxy over OpenCode's SSE; assistant messages are persisted to `ai_chat_messages` on `message.finished`. The frontend `EventSource` auto-reconnects (1s→2s→5s→10s).
*   **Design intent**: adding a skill to OpenCode grants the frontend capability with zero frontend changes (generic tool-call rendering). M1 renders tool calls as plain JSON; the `ToolCallBubble` + `tool-renderers` registry is deferred to M2.

Key files: `server/routes/ai_chat.py` (`ai_chat_bp`: sessions/messages/SSE/delete), `server/utils/opencode_client.py`, `server/utils/workspace.py` (path-traversal-safe per-session dirs), `server/utils/session_token.py`, `mcp-server/` (standalone FastAPI + MCP Streamable-HTTP service, run from its own `.venv`), `src/components/ai-chat/` (drawer, message list/item, markdown view, input), `src/stores/aiChat.ts`, `src/api/aiChat.ts`. Design + plan: `docs/superpowers/specs/2026-05-26-ai-agent-frontend-design.md`, `docs/superpowers/plans/2026-05-26-ai-agent-frontend-m1.md`. E2E smoke: `e2e/ai-chat-smoke.spec.ts` (`npm run test:e2e`, requires OpenCode + MCP running). 长期记忆（M1）：`server/utils/memory.py` 用 mem0（Chroma + DashScope 兼容端点）按 `user_id` 管理跨会话记忆；`send_message` 转发前注入、SSE `idle` 落库后由 `extract_from_turn` 后台抽取；开关 `ai_settings.mem0_enabled`。全程降级，记忆层异常不影响聊天。M2：MCP 工具 `memory_search/add/delete`（`mcp-server/tools/memory.py`）经 Flask 内部端点 `/ai/memory/internal/*`（`MCP_INTERNAL_TOKEN` 鉴权，`routes/ai_memory_internal.py`）转发，Flask 仍是 Chroma 唯一写入方。

### AI Agent Chat — Batch Tasks (M1.5)

Pick N files + 1 prompt → N isolated sessions, throttled to 3 concurrent. Children are real `ai_chat_sessions` rows with `batch_id` / `batch_seq` / `batch_input_file` set; the existing chat view handles them as-is, so clicking a child from the batch dashboard opens its full conversation thread.

*   **Worker**: `server/utils/batch_engine.py` — in-process `BatchWorker` (daemon thread + `ThreadPoolExecutor(3)`), started from `app.py` next to the existing schedulers under `WERKZEUG_RUN_MAIN`. Claims FIFO via `FOR UPDATE SKIP LOCKED`. 30-min per-child timeout. On Flask restart, audit resets orphaned `running` rows back to `pending`.
*   **REST**: `server/routes/ai_chat_batches.py` (POST/GET/DELETE/`retry-failed` + staging upload), `server/routes/ai_chat_prompt_templates.py` (per-user CRUD). Both registered after `ai_chat_bp` and before `dynamic_bp` (which stays last).
*   **Frontend**: AI 助手 sidebar gains a `会话 / 批任务` tab switch. `BatchListView` + `BatchDetailView` + `CreateBatchDialog` + `PromptTemplateManager`. Detail polled every 5 s, list every 10 s; both pause on `document.hidden` and resume on visible. Polling stops on terminal states (`completed` / `failed`).
*   **Failure policy**: failed children are red-flagged and never abort the batch; `POST /ai/chat/batches/:id/retry-failed` resets failures to `pending` for the worker to pick up. No auto-retry.

Key files: `server/utils/batch_repo.py`, `server/utils/batch_engine.py`, `server/utils/prompt_template.py`, `server/routes/ai_chat_batches.py`, `server/routes/ai_chat_prompt_templates.py`, `src/types/aiChatBatch.ts`, `src/stores/aiChatBatches.ts`, `src/views/ai-chat/BatchListView.vue`, `src/views/ai-chat/BatchDetailView.vue`, `src/components/ai-chat/CreateBatchDialog.vue`, `src/components/ai-chat/PromptTemplateManager.vue`. Design + plan: `docs/superpowers/specs/2026-05-31-ai-chat-batch-tasks-design.md`, `docs/superpowers/plans/2026-05-31-ai-chat-batch-tasks.md`. E2E: `e2e/ai-chat-batch.spec.ts`.

### AI Scheduled Tasks (定时 AI 数据流水线)

A **generic** config-driven paradigm (audit is one instance): on a schedule, scan a data page, hand each pending record (fields + attached documents) to an AI session with a prompt/skill, parse its structured JSON, and write results back into the record while flowing a **status field** 待处理 → 处理中 → 已处理/处理失败. See `docs/user-guide/ai/scan-tasks.md`.

*   **Reuses the AI batch engine**: each scan = one batch, each record = one child session (stamped `scan_task_id` + `source_record_id`); a completion hook in `batch_engine._run_one` calls `ai_scan_engine.on_child_finished` to parse + write back.
*   **Scheduler** `server/utils/ai_scan_scheduler.py` (APScheduler 1-min tick, per-task due/lock, startup orphan sweep, `WERKZEUG_RUN_MAIN` guard). **Engine** `server/utils/ai_scan_engine.py` (atomic `FOR UPDATE SKIP LOCKED` claim→处理中; context dir = `record.md` + `attachments/`; prompt = your template + auto-appended JSON contract from the field mapping; write-back via parameterized `jsonb_set`). **Repo/API** `server/utils/ai_scan_repo.py`, `server/routes/ai_scan_tasks.py` (CRUD + run-now, `@require_permission('admin.ai_scan')`).
*   **Config table** `ai_scan_tasks`; admin page `src/views/admin/AiScanTaskManager.vue` (`/admin/ai-scan-tasks`). Cross-table side effects are left to the skill via MCP; only same-row structured write-back is built in. Design + plan: `docs/superpowers/specs/2026-06-03-scheduled-ai-row-processor-design.md`, `docs/superpowers/plans/2026-06-03-scheduled-ai-row-processor.md`.

### Database Design

*   **`dynamic_data`**: The core table. `data` column holds all fields as JSONB. Has `branch_id` for version isolation.
*   **`data_relations`**: Stores M:N relationships. Queried by `(collection, record_id, field_name)`.
*   **`page_configs`**: The schema definition. `fields` JSONB column drives the entire UI.
*   **`menus`**: Tree structure with `roles` JSONB for RBAC. Three types: `workspace` (level 1), `project` (level 2), `data` (level 3).
*   **`users`**: Accounts with `role` (admin / developer / guest).
*   **`project_versions`**: Project-level branches/snapshots. Tracks status (`active`, `merged`, `archived`), parent version, and lock state.
*   **`project_dependencies`**: Cross-project dependency declarations. Three relation types: `track-main`, `read-write`, `read-only`.
*   **`project_dependency_relations`**: Records which data relations are involved in each dependency.
*   **`webhook_rules`**: Event-triggered webhooks. Supports `create`, `update`, `delete`, `merge` events with HMAC-SHA256 signatures.
*   **`webhook_logs`**: Webhook execution history with retry tracking.
*   **`notifications`**: User notifications including dependency alerts (`dependencyBroken`, `dependencyWarning`, `dependencyResolved`).
*   **`column_views`**: Per-page saved column-view configurations (visibility, order, width, filters). Frontend reloads these on keep-alive reactivation to prevent cross-page view leak (see commit `932b2d6`).
*   **`ai_chat_sessions`** / **`ai_chat_messages`**: AI Agent chat sessions (opaque `session_token`, `workspace_path`, `opencode_session_id`, status) and their messages (`content` JSONB of typed parts). See the AI Agent Chat section above.
*   Other tables: `export_scripts`, `validation_scripts`, `etl_tasks`, `etl_logs`, `api_keys`, `operation_logs`, `backups`, `backup_settings`, `ai_settings`, `collection_versions`, `version_snapshots`, `dashboards`, `record_comments`, `trigger_rules`, `system_config`, `home_widgets`.

### Backend Structure

*   `app.py`: Registers ~35 blueprints (auth, users, menus, page_configs, relations, operation_logs, backups, export_scripts, api_keys, open_api, validation_scripts, etl_tasks, relation_graph, query, comments, timeline, dashboards, notifications, trigger_rules, ai, project_versions, cross_project_deps, webhook, menu_export, system_config, home_widgets, column_views, ai_chat, ai_chat_prompt_templates, ai_chat_batches, ai_scan_tasks, data_files, roles, workflows, dynamic). **Order matters**: `dynamic_bp` (catch-all `/<collection>`) must be registered last to avoid shadowing specific routes; the AI-chat/scan and admin blueprints (`ai_chat`, `ai_scan_tasks`, `roles`, `workflows`, …) are all registered before it.
*   Four background components start in `app.py` (guarded by `WERKZEUG_RUN_MAIN` to avoid double-start in Flask reloader): `start_backup_scheduler` (scheduled backups), `start_dependency_scheduler` (hourly cross-project dependency revalidation), the in-process AI batch worker (`get_worker().start()`), and `start_scan_scheduler` (scheduled AI scan tasks).
*   `routes/dynamic.py`: The generic CRUD handler. Supports pagination (`page`, `pageSize`, `all=true`), filtering (`q` for MongoDB-style query, `keyword` for full-text search). Validates primary key uniqueness and manages relations.
*   `routes/project_versions.py`: Project-level branch management. Create, merge, delete, lock/unlock branches.
*   `routes/cross_project_dependencies.py`: Dependency declaration CRUD, validation, merge dependency check, branch delete protection.
*   `routes/webhooks.py`: Webhook rule management, test execution, log retrieval.
*   `utils/script_runner.py`: Sandboxed Python execution for validation/export scripts.
*   `auth.py`: JWT decorators — `login_required`, `write_required` (blocks guest), `require_permission('admin.x')` (RBAC capability gate; replaced the old `admin_required`), `api_key_required`. See `utils/permissions.py` + `utils/rbac_guard.py` and `docs/user-guide/admin/roles-rbac.md`.
*   `db.py`: `psycopg2.pool.ThreadedConnectionPool` (2–20 connections, thread-safe). Use the `get_db()` context manager. Imported as `from db import get_db`. The production backend runs under waitress with a bounded thread pool (`BACKEND_THREADS`, default 8) kept below `maxconn`.

**Reserved collection paths** (cannot be used as dynamic data collection names; authoritative list lives in `RESERVED` at `server/routes/dynamic.py:18`): `menus`, `pageConfigs`, `relations`, `auth`, `users`, `roles`, `operationLogs`, `backups`, `exportScripts`, `apiKeys`, `validationScripts`, `etlTasks`, `relation-graph`, `query`, `comments`, `timeline`, `dashboards`, `notifications`, `triggerRules`, `ai`, `versions`, `project-versions`, `webhook`, `dependencies`, `system-config`, `home-widgets`, `data-files`, `ai-scan-tasks`, `favicon.ico`.

### Frontend Structure

*   `src/stores/pageConfig.ts`: The central store. Contains critical logic for:
    *   Resolving relation/reference/quote field imports.
    *   Generating auto-sequence values.
    *   Batch data operations.
*   `src/stores/menu.ts`: Menu tree with shallowRef caching + role-based filtering.
*   `src/stores/auth.ts`: JWT token management. localStorage keys prefixed `check-manage:`.
*   `src/components/dynamic-form/controls/`: Field control components. Mapped by `controlType`.
*   `src/api/`: Axios-based API layer. Base URL `/api`, 30s timeout. Request interceptor injects Bearer token.

### API Proxy

Vite proxies `/api` to backend port 3002 **with path rewrite**: frontend calls `/api/menus` → backend receives `/menus`. This is why backend routes don't have an `/api` prefix.

### User Roles (Customizable RBAC)

Roles are **data-driven and customizable** (see `docs/user-guide/admin/roles-rbac.md`). Permissions are controlled at three granularities: **admin-feature toggles** (`admin.*` capability keys), **per-data-page CRUD**, and **menu visibility** (`menus.roles`).

*   Built-in seeds: **admin** (permanent superuser — all permissions, undeletable), **developer** (read/write data, no admin features), **guest** (read-only). `developer`/`guest` are editable; custom roles can be added.
*   Backend is authoritative: `@require_permission('admin.x')` (replaced `admin_required`) + `require_page_action()` in `routes/dynamic.py`/`relations.py`. Resolution + cache in `server/utils/permissions.py`; catalog in `PERMISSION_CATALOG`.
*   Frontend gating (UX only): `auth` store `isSuperuser`/`can()`/`canPage()`; `/admin/roles` role manager. JWT carries only the role slug — permission edits take effect on next `/auth/me` without re-login.
*   ⚠️ **Capability ≠ menu visibility**: granting an `admin.*` capability lets a role *access* a route, but the sidebar link only shows if the role slug is in that menu's `menus.roles`.

### Menu Structure (Standard 3-Layer Hierarchy)

Menus follow a strict 3-level hierarchy enforced by `menuType` field:

| Level | menuType | Description | Example |
|-------|----------|-------------|---------|
| 1 | `workspace` | Top-level container, no `page_id` | "测试工作空间" |
| 2 | `project` | Project node, children are data pages | "项目A" |
| 3 | `data` | Data page, has `page_id` pointing to `page_configs` | "测试订单" |

Projects can be direct children of workspace (level 2) OR standalone level-1 menus with `menuType='project'`. Data pages must have valid `page_id` and `parent_id` pointing to a project.

## Development Workflow

### ⚠️ Documentation Sync (MANDATORY)

**Every user-facing feature change MUST update the user guide in the same PR.** When you add, change, or remove a feature that a user interacts with, update the matching doc under `docs/user-guide/` (organized by feature: `getting-started/ data/ admin/ integration/ ai/`, English-slug filenames). If no doc exists for the feature, create one in the right subfolder and link it from `docs/user-guide/README.md` (the index/TOC). Where a flow has UI, include a real page screenshot under `docs/user-guide/_images/`. Treat the doc update as part of "done" — a feature change without its user-guide update is incomplete.

### Adding a New Business Entity (e.g., "Products")

**Do NOT** create a new Vue file or SQL table.

1.  Insert a row into `page_configs`:
    ```sql
    INSERT INTO page_configs (id, name, fields)
    VALUES ('page-products', '产品管理', '[{"fieldName": "name", "label": "产品名称", "controlType": "text", ...}]');
    ```
2.  Insert a row into `menus`:
    ```sql
    INSERT INTO menus (id, name, page_id, path, parent_id, "order")
    VALUES ('menu-products', '产品管理', 'page-products', '/products', NULL, 1);
    ```
3.  Done. The system creates the route `/products`, the API `GET/POST /products`, and the UI.

### Adding a New Field Control Type

1.  **Frontend**: Create `src/components/dynamic-form/controls/NewControl.vue`.
2.  Register it in `DynamicForm.vue` and `DataTable.vue` column rendering.
3.  Update `ControlType` in `src/types/field.ts` and re-export from `src/types/index.ts`.
4.  **Backend**: If validation is needed, update `routes/dynamic.py` validation logic.

### Script Runner Sandbox

Validation and export scripts execute in a sandboxed Python environment (`utils/script_runner.py`):
*   **Timeout**: 60s for row-level scripts, 300s for collection-level scripts.
*   **Forbidden**: `import`, `eval`, `exec`, `open`, `getattr`, `type`, etc.
*   **Pre-injected modules**: `json`, `csv`, `io`, `re`, `math`, `collections`, `xml.etree.ElementTree`, `datetime`, `pandas` (as `pd`), `numpy` (as `np`).
*   **No `print()`** — scripts communicate via return values.

### Creating Test Dependency Data

To test cross-project dependency features, run the test data creation script:

```bash
cd server && python create_test_dependency_data.py
```

This creates:
*   Workspace "测试工作空间" with 3 test projects (A, B, C)
*   Versions for each project (merged and active branches)
*   Four dependency declarations demonstrating all three types
*   Test data pages with relation fields (订单 → 产品 → 客户)
*   Data relations between collections across projects

## Key Technical Details

*   **Authentication**: JWT in `Authorization: Bearer <token>` header. Middleware decorators in `server/utils/auth.py`.
*   **Default Credentials**: admin / admin123 (set in `server/seed_data.py`).
*   **Database Config**: `server/config.py` — PostgreSQL on localhost:5432, database `casemanage`.
*   **TypeScript**: Strict mode enabled (`noUnusedLocals`, `noUnusedParameters`). Path alias `@/` → `src/`.
*   **SCSS**: Global variables via `src/assets/styles/variables.scss` (auto-imported in all SCSS).
*   **Production**: `start.sh` builds frontend, starts backend + reverse proxy on port 8080. The backend is served by **waitress** (bounded thread pool, `BACKEND_THREADS` env, default 8 — kept below the DB pool's `maxconn=20`); the dev server (`npm run dev`) stays on Werkzeug with `threaded=True`.

## Testing Patterns

Frontend tests: `src/**/__tests__/**/*.test.ts` with jsdom environment. Setup file: `src/test/setup.ts` (provides localStorage mock).

### ResizeObserver Polyfill
jsdom environment lacks ResizeObserver. Add this polyfill in test files when components use Element Plus or resize-dependent features:
```typescript
beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})
```

### Element Plus Component Stubs
For isolated component testing, stub Element Plus components with minimal implementations:
```typescript
const stubs = {
  'el-checkbox': {
    template: `<input type="checkbox" :checked="modelValue" @change="$emit('change', $event.target.checked)" />`,
    props: ['modelValue'],
    emits: ['change'],
  },
  'el-button': {
    template: `<button @click="$emit('click')"><slot /></button>`,
    emits: ['click'],
  },
}
```

### Deep Equality Assertions
Use `toStrictEqual` for object comparisons (not `toBe`):
```typescript
// ❌ Wrong - fails for objects
expect(state.sourceVersion).toBe(version)
// ✅ Correct - deep equality
expect(state.sourceVersion).toStrictEqual(version)
```

## Composables Pattern

Use Vue 3 composables for reusable state management. Key patterns:
- Return reactive state and computed properties
- Provide action functions that modify state
- Use `reactive()` for complex state objects
- Use `Set`/`Map` for collection state (more intuitive than arrays for toggle operations)
- Distinguish between data existence (`hasDiff`) and user action state (`hasSelection`)