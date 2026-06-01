# AI Chat Batch Tasks — Design Spec

> **Status:** Approved
> **Date:** 2026-05-31
> **Owner:** jayKim (jinjie1231@gmail.com)
> **Branch:** feat/ai-agent-m1 (or a follow-on branch derived from it)

## Problem

The user has high-volume "one prompt × many input files" workflows — e.g. 20 巡检指导书 (PDF/DOCX) each needing the same "开发巡检用例" task. The current AI Chat only supports one session at a time. Doing all 20 in a single session bloats context and risks the model going off-track late in the run. Manually opening 20 sessions, uploading 20 files, and typing the same prompt 20 times is the obvious bad workaround.

We need a **batch-task primitive**: pick N files, write 1 prompt, get N isolated sessions running concurrently (throttled), with a dashboard to track progress and retry failures.

## Goals (in scope for v1)

1. Create a batch from a name + multiple uploaded files + a prompt. Each file becomes its own isolated AI Chat session.
2. Throttle to **3 concurrent** active sessions per server; queued sessions wait.
3. Persist **prompt templates** per user (re-usable across batches).
4. Show a **批任务 tab** in the AI Chat sidebar alongside the existing 会话 tab, with a list of batches and a detail panel for the selected batch.
5. **Failure isolation**: a single failed session does not abort the batch. Failed sessions are marked red; a "重试失败" button on the batch detail panel re-queues all failed sessions in that batch.
6. From the batch detail panel, clicking a child session jumps back to the 会话 tab with that session selected (the user can then read the full conversation as if they had created it manually).

## Non-Goals (deferred)

- Real-time SSE for batch state — v1 polls.
- Template sharing / pinning / cross-user visibility — v1 is per-user only.
- Pause / resume / clone a batch.
- Batch name + prompt editing after creation.
- Bulk-download all batch outputs as a ZIP.
- Batch-level scheduling (e.g. "start at 22:00").
- Per-user concurrency override — the 3-cap is server-wide.

## Architecture

```
Browser                Flask              OpenCode HTTP API
   │                     │                       │
   │ POST /batches ──────▶                       │
   │                     │── insert batch row    │
   │                     │── insert N session    │
   │                     │   rows (status=       │
   │                     │   pending)            │
   │                     │── notify worker       │
   │ ◀── 201 Created ────│                       │
   │                     │                       │
   │ GET /batches/:id ───▶ poll every 5s         │
   │                     │                       │
   │              (background batch worker)      │
   │                     │  ┌─ pick pending ────▶│ create session
   │                     │  │  → write workspace │ upload file
   │                     │  │  → send prompt     │
   │                     │  │  → poll messages   │ ◀── message.finished
   │                     │  │  → mark done       │
   │                     │  └─ rinse, max 3 ─────│
```

**Key design choice**: the worker is an in-process daemon thread (like the existing backup/dependency schedulers — see `server/app.py`), not a Celery/RQ service. Lowest ops complexity and the workload (max 3 long-running HTTP poll loops) is well within Flask's threading model.

**Workspace per child session**: each child session still gets its own per-session workspace dir from the existing `create_session_workspace` flow — the batch doesn't change session isolation, only how sessions are spawned.

## Data Model

### New table — `ai_chat_batches`

```sql
CREATE TABLE ai_chat_batches (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id),
  name        TEXT NOT NULL,
  prompt      TEXT NOT NULL,
  template_id UUID NULL REFERENCES ai_chat_prompt_templates(id) ON DELETE SET NULL,
  status      TEXT NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','running','completed','partial','failed')),
  total       INT  NOT NULL DEFAULT 0,
  done        INT  NOT NULL DEFAULT 0,
  failed      INT  NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at  TIMESTAMPTZ NULL
);
CREATE INDEX idx_ai_chat_batches_user_created
  ON ai_chat_batches(user_id, created_at DESC);
```

**Status transitions** (derived from child session counts):
- `pending` — created, no child has started yet (`done+failed = 0` and no running children).
- `running` — at least one child is running OR all children are queued waiting.
- `completed` — `done == total` (no failures).
- `partial` — `done + failed == total` AND `failed > 0`.
- `failed` — `failed == total` (every child failed).

Status is **computed and persisted by the worker** on every child state change (not a database view) so list queries are O(1).

### New table — `ai_chat_prompt_templates`

```sql
CREATE TABLE ai_chat_prompt_templates (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES users(id),
  name       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ai_chat_prompt_templates_user
  ON ai_chat_prompt_templates(user_id, updated_at DESC);
CREATE UNIQUE INDEX uniq_template_user_name
  ON ai_chat_prompt_templates(user_id, name);
```

### Modified table — `ai_chat_sessions`

Add three nullable columns (backward compatible — existing single-session sessions have `batch_id = NULL`):

```sql
ALTER TABLE ai_chat_sessions
  ADD COLUMN batch_id         UUID NULL REFERENCES ai_chat_batches(id) ON DELETE CASCADE,
  ADD COLUMN batch_seq        INT  NULL,
  ADD COLUMN batch_input_file TEXT NULL;

CREATE INDEX idx_ai_chat_sessions_batch
  ON ai_chat_sessions(batch_id, batch_seq);
```

- `batch_id` — FK to batch. `ON DELETE CASCADE` so deleting a batch cleans up all children.
- `batch_seq` — 0..N-1, stable order matching the order files were uploaded.
- `batch_input_file` — workspace-relative path of the file assigned to this session (e.g. `uploads/指导书-A.pdf`). This is the file the agent should reason about.

`ai_chat_sessions.status` already exists; the worker drives it through:
`pending` → `running` → `completed` | `failed`.

## REST API

All endpoints behind existing `login_required`. Routes registered just before `dynamic_bp` (catch-all).

### Batches — `server/routes/ai_chat_batches.py`

```
POST   /ai/chat/batches
  body:
    {
      name:       string,           // batch display name
      prompt:     string,           // the task prompt sent to every child
      template_id: string | null,   // optional, for analytics/UX continuity
      files:      [                 // already-uploaded files (see "Upload flow")
        { name: string, path: string },
        ...
      ]
    }
  effect:
    - one INSERT into ai_chat_batches
    - N INSERTs into ai_chat_sessions (status=pending, batch_id, batch_seq, batch_input_file)
    - signal worker (Event.set() on the in-proc worker)
  returns:
    { batch: <batch row>, sessions: [<session row>, ...] }
  errors:
    400 — empty files, empty prompt, empty name, files > 50
    413 — total uploaded bytes > server cap (reuse existing upload cap)

GET    /ai/chat/batches?page=1&pageSize=20
  returns list of the current user's batches, newest first.

GET    /ai/chat/batches/:id
  returns batch row + full list of child sessions (id, batch_seq, batch_input_file,
  status, last_message_preview, opencode_session_id, error_message).

DELETE /ai/chat/batches/:id
  cascades to child sessions; the existing per-session cleanup
  (workspace dir teardown, MCP token revocation) MUST run for each child.

POST   /ai/chat/batches/:id/retry-failed
  resets every child session with status=failed back to pending,
  decrements ai_chat_batches.failed by the same count,
  re-signals worker.
  returns { retried: <count> }
```

### Prompt templates — `server/routes/ai_chat_prompt_templates.py`

```
GET    /ai/chat/prompt-templates              list current user's templates
POST   /ai/chat/prompt-templates              { name, content } → create
PUT    /ai/chat/prompt-templates/:id          { name, content } → update
DELETE /ai/chat/prompt-templates/:id          delete
```

Name is unique per user (DDL constraint); duplicate POST/PUT returns 409.

### Upload flow

We reuse the existing per-session upload endpoint **but** route uploads for a not-yet-created batch into a staging dir.

Add: `POST /ai/chat/batches/staging/upload` — multipart upload, returns
`{ name, path }` where `path` is relative to the staging root
(`<workspace_root>/batch-staging/<user_id>/<uuid>/<filename>`).

When the batch is created, the worker **moves** each staged file into the child session's `uploads/` dir (or copies; copy is simpler and lets us delete the staging dir non-blockingly). After move, the staging dir for that batch creation is deleted.

A nightly sweep (piggyback on existing `start_backup_scheduler` housekeeping or a new cron) removes staging dirs older than 24h that never resolved to a batch.

## Worker / scheduler

**File**: `server/utils/batch_engine.py`

**Lifecycle**: started in `server/app.py` next to the existing schedulers, guarded by `WERKZEUG_RUN_MAIN` to avoid double-start under Flask reloader.

**Shape**:
```python
class BatchWorker:
    MAX_CONCURRENT = 3
    POLL_INTERVAL_SEC = 2
    SESSION_TIMEOUT_SEC = 1800   # 30 min per child

    def __init__(self):
        self._wake = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=self.MAX_CONCURRENT)
        self._running_session_ids = set()   # protected by self._lock
        self._lock = threading.Lock()

    def start(self): ...     # spawns the dispatcher daemon thread
    def notify(self): self._wake.set()

    def _dispatcher_loop(self):
        while True:
            self._wake.wait(timeout=10)   # also wake every 10s as a safety tick
            self._wake.clear()
            pending = self._claim_pending_sessions(
                limit=self.MAX_CONCURRENT - len(self._running_session_ids)
            )
            for s in pending:
                self._executor.submit(self._run_one, s)

    def _run_one(self, session_row):
        # 1. mark running, register in self._running_session_ids
        # 2. create workspace, copy staged file into uploads/
        # 3. ask OpenCode to start a session bound to that workspace
        # 4. send the batch prompt as the first user message
        # 5. poll OpenCode messages every POLL_INTERVAL_SEC until message.finished
        #    OR timeout OR a hard error
        # 6. write status (completed/failed) back to DB,
        #    update parent batch counters atomically (single UPDATE),
        #    recompute parent batch status,
        #    drop from self._running_session_ids,
        #    notify()  ← lets the dispatcher start the next queued one immediately
```

**Concurrency invariant**: the 3-cap is a server-wide cap on `_running_session_ids` size. The dispatcher only `_claim_pending_sessions(limit=remaining_capacity)`, where claim uses
`UPDATE ... SET status='running' WHERE id IN (SELECT id FROM ... WHERE status='pending' ORDER BY created_at, batch_seq LIMIT $1 FOR UPDATE SKIP LOCKED) RETURNING *;`
This ensures (a) no double-pickup, (b) FIFO across batches (a 20-file batch doesn't starve a 1-file batch — see Fairness below).

**Fairness**: pure FIFO by `(created_at, batch_seq)` means a 20-file batch submitted at 10:00 will block a 1-file batch submitted at 10:01 for some time. v1 accepts this; the workload pattern is "occasional large batch", not "many concurrent users". A v2 fairness improvement (round-robin per batch) is noted in Open Questions.

**Failure classification**:
- `requests.HTTPError` 4xx/5xx from OpenCode → `failed`, `error_message` = HTTP status + body excerpt.
- timeout (`SESSION_TIMEOUT_SEC`) → `failed`, `error_message = "timeout after 30m"`.
- exception in worker code → caught, logged, session → `failed`, error_message = exception class.

**Restart safety**: on Flask startup, the worker resets any `status='running'` session whose OpenCode side cannot be confirmed alive back to `pending` (this is a small audit query at boot). Sessions that were `done` or `failed` stay as-is.

## Frontend

### Routing & state

No new top-level route. `/ai-chat` continues to host everything. New tab state inside the sidebar.

### Components

```
src/views/ai-chat/
  AiChatView.vue           (modified: add Tab switch in sidebar header)
  BatchListView.vue        (new: left-pane batch list, replaces session list when tab=batches)
  BatchDetailView.vue      (new: right-pane batch dashboard, replaces chat thread when batch selected)

src/components/ai-chat/
  CreateBatchDialog.vue    (new: multi-file upload + prompt + template picker)
  PromptTemplateManager.vue (new: drawer for CRUD templates)

src/api/
  aiChatBatches.ts
  aiChatPromptTemplates.ts

src/stores/
  aiChatBatches.ts         (new: list, detail, polling, retry, create)
  aiChat.ts                (modified: expose jumpToSession(sessionId) used by BatchDetailView)

src/types/
  aiChatBatch.ts           (new: AiChatBatch, AiChatBatchSession types)
```

### Sidebar tab

`AiChatView.vue` gets a tab switch in the sidebar header:

```
┌────────────────────────┐
│ [会话] [批任务]         │  ← Element Plus segmented control
│ ─────────────────────  │
│ + 新建会话 / + 新建批任务│  (label changes with tab)
│                        │
│ list...                │
└────────────────────────┘
```

State: `activeSidebarTab: 'sessions' | 'batches'` in `AiChatView.vue`.

### Batch list (left pane when tab=batches)

Each card:
```
┌──────────────────────────────────────┐
│ 巡检指导书开发批次 - Q2              │
│ ━━━━━━━━░░░░  8/20                  │
│ [运行中] 2 失败 · 12 分钟前           │
└──────────────────────────────────────┘
```
Active card highlighted (same pattern as session list).

### Batch detail (right pane when batch selected)

```
┌─────────────────────────────────────────────────────────────┐
│ 巡检指导书开发批次 - Q2          [运行中]   [重试失败 (2)]  [删除]│
│ ─────────────────────────────────────────────────────────── │
│ Prompt: 开发巡检用例…（展开/折叠全文）                        │
│ ━━━━━━━━━━━━━░░░░░░  完成 8 / 失败 2 / 进行中 3 / 总计 20    │
│ ─────────────────────────────────────────────────────────── │
│ #  状态      文件                       最近消息             │
│ 1  ✅ 完成   指导书-A.pdf               已生成 12 条用例       │
│ 2  ✅ 完成   指导书-B.pdf               已生成 9 条用例        │
│ 3  ❌ 失败   指导书-C.pdf               OpenCode timeout       │
│ 4  🔄 运行中  指导书-D.pdf              正在分析章节 3…        │
│ 5  ⏳ 排队   指导书-E.pdf               —                     │
│ ...                                                          │
└─────────────────────────────────────────────────────────────┘
```

Click on a row → switches sidebar tab back to `sessions` AND selects that session in the session list (the user sees the full conversation, can intervene, copy, retry — same as a manually-created session).

### Create batch dialog

```
┌─ 新建批任务 ────────────────────────────────────┐
│ 批任务名 [_________________________________]    │
│                                                 │
│ 模板    [ 选择已保存模板 ▾ ] [管理模板]          │
│ Prompt  ┌──────────────────────────────────┐    │
│         │                                  │    │
│         │                                  │    │
│         └──────────────────────────────────┘    │
│         [□ 保存为模板 名称: _________ ]         │
│                                                 │
│ 文件    [+ 选择文件...]   (已选 0 / 上限 50)    │
│         · 指导书-A.pdf         ✕                │
│         · 指导书-B.pdf         ✕                │
│         · ...                                   │
│                                                 │
│                          [取消]  [创建]         │
└─────────────────────────────────────────────────┘
```

- Files selected → immediately uploaded to staging (progress per file). Upload failures are surfaced; the user can remove and re-add.
- "创建" button is disabled until: name non-empty, prompt non-empty, ≥1 file fully uploaded.
- "保存为模板" + 名称 non-empty → after batch creation succeeds, POST `/prompt-templates` (best-effort; failure is a toast, not a block).

### Template manager drawer

Standard CRUD table; reachable from "管理模板" in the create dialog and from a future menu entry. Out of v1: import/export of templates.

### Polling

When a batch detail is open:
- `GET /ai/chat/batches/:id` every **5 seconds**.
- Stop polling when batch status is `completed` or `failed` (terminal).
- Continue polling while `pending` / `running` / `partial`. (`partial` is a terminal state UI-wise but might transition back to `running` if the user clicks 重试失败 — so partial keeps polling for ~1 cycle after a retry, then stops if no new transitions; simplification: just keep polling for partial, cheap.)
- On tab hide (`document.visibilitychange`), pause polling; resume on tab visible.

Batch list polling: every **10 seconds**, only while the batch tab is active.

### Jump-to-session

`useAiChatStore().jumpToSession(sessionId)` (new action):
1. Switch `activeSidebarTab` to `sessions`.
2. Ensure session list contains the target session (refetch if necessary — batch children are real `ai_chat_sessions` rows so they show up in the list automatically).
3. Select the session.
4. Scroll the session list to that item.

## Failure handling — comprehensive

| Where | What goes wrong | Visible to user as | Recoverable how |
|-------|-----------------|--------------------|-----------------|
| Staging upload | network drop mid-upload | per-file error in dialog | user removes and re-adds |
| Batch creation | DB insert fails | dialog toast + dialog stays open | user retries 创建 |
| Worker — workspace create | disk full, permission | session.status=failed, error_message | 重试失败 |
| Worker — OpenCode session create | OpenCode down, 5xx | failed, error_message=HTTP status | 重试失败 |
| Worker — prompt send | OpenCode 4xx/5xx | failed | 重试失败 |
| Worker — polling | OpenCode message.finished never arrives | failed after `SESSION_TIMEOUT_SEC` | 重试失败 |
| Worker — exception | bug | failed, error_message=exception type | 重试失败 (might re-hit the bug) |
| Flask restart mid-batch | running session orphaned | restart audit resets to pending | worker resumes automatically |
| Batch delete | user deletes mid-run | CASCADE removes children + workspaces | n/a |

## Security

- Every batch endpoint checks `batch.user_id == g.current_user.id` (admin can read all, like the existing dependency endpoints).
- Staging path resolution uses the existing `safe_resolve` helper from `server/utils/workspace.py` (path-traversal defense).
- File names from upload are sanitized (existing upload pipeline already does this — reuse).
- Prompt content is text, not executed; no injection surface beyond what OpenCode itself accepts.

## Testing strategy

### Backend

- `tests/test_batch_engine.py`
  - `_claim_pending_sessions` honors the limit and FIFO order
  - `_run_one` happy path: mocked OpenCode → status transitions correctly, batch counters update
  - failure: HTTP 500 from OpenCode → `failed`, `error_message` populated
  - timeout: `SESSION_TIMEOUT_SEC` reached → `failed` with "timeout"
  - parent status computation: 20 sessions, mix of done/failed/running → correct enum
  - concurrency cap: 5 pending, only 3 in `_running_session_ids` at any moment
  - restart audit: pre-seed a `running` session, start worker, assert it flips to `pending`

- `tests/test_ai_chat_batches.py` (route tests using existing fixture pattern)
  - POST creates batch + child rows; reject empty fields; reject >50 files
  - GET list scopes to user
  - GET detail returns children with `batch_seq` order
  - DELETE cascades (assert child rows gone)
  - retry-failed resets only `failed` children, decrements counter

- `tests/test_prompt_templates.py`
  - CRUD round trip
  - per-user uniqueness on name
  - other user's templates not visible

### Frontend

- `src/stores/__tests__/aiChatBatches.test.ts`
  - polling start/stop on detail open/close
  - polling pause on tab hidden, resume on visible
  - jumpToSession switches tab + selects session
  - retry-failed optimistically clears `failed` count, refetches on response

- `src/components/ai-chat/__tests__/CreateBatchDialog.test.ts`
  - 创建 button disabled until name + prompt + ≥1 uploaded file
  - upload failure of one file doesn't break others
  - "保存为模板" triggers template create after batch create succeeds

E2E (Playwright, `e2e/ai-chat-batch.spec.ts`):
- Login → AI 助手 → switch to 批任务 tab → 新建批任务 → upload 3 files → enter prompt → 创建 → batch shows in list → open detail → all 3 children transition to a terminal state → counters match.

## File map (for the implementation plan)

**Created**
- `server/utils/batch_engine.py`
- `server/utils/prompt_template.py`
- `server/routes/ai_chat_batches.py`
- `server/routes/ai_chat_prompt_templates.py`
- `server/tests/test_batch_engine.py`
- `server/tests/test_ai_chat_batches.py`
- `server/tests/test_prompt_templates.py`
- `src/api/aiChatBatches.ts`
- `src/api/aiChatPromptTemplates.ts`
- `src/stores/aiChatBatches.ts`
- `src/stores/__tests__/aiChatBatches.test.ts`
- `src/types/aiChatBatch.ts`
- `src/views/ai-chat/BatchListView.vue`
- `src/views/ai-chat/BatchDetailView.vue`
- `src/components/ai-chat/CreateBatchDialog.vue`
- `src/components/ai-chat/__tests__/CreateBatchDialog.test.ts`
- `src/components/ai-chat/PromptTemplateManager.vue`
- `e2e/ai-chat-batch.spec.ts`

**Modified**
- `server/app.py` — register two new blueprints; start `BatchWorker` next to existing schedulers.
- `server/init_db.py` — DDL for new tables + ALTER on `ai_chat_sessions`.
- `server/routes/dynamic.py` — add `'batches'` and `'prompt-templates'` to the `RESERVED` list (under the AI chat path prefix this is moot since they're under `/ai/chat/`, but keep RESERVED self-consistent — they're new collection-shaped paths only at the AI chat namespace, no change needed to RESERVED).
- `src/views/ai-chat/AiChatView.vue` — add sidebar tab switch; conditionally render `<BatchListView>` / `<SessionList>` and `<BatchDetailView>` / `<ChatThread>`.
- `src/stores/aiChat.ts` — add `jumpToSession(id)` action.
- `CLAUDE.md` — append a short "AI Chat batch tasks" section under "AI Agent Chat (M1)".

## Open questions (for future iterations, do NOT block v1)

1. **Fairness across batches**. v1 is FIFO `(created_at, batch_seq)`. A v2 round-robin would interleave children from competing batches so a fresh small batch doesn't wait for a large one to drain. Need data on actual usage before deciding.
2. **Per-batch concurrency override**. The 3-cap is global. If two power users start 20-file batches simultaneously, one waits. v2 could let users opt into "share evenly" vs "first-come-first-served".
3. **Batch SSE**. v1 polls. If the dashboard feels laggy or the polling load becomes visible in profiling, switch to a per-batch `EventSource` that emits child-state-change events.
4. **Resumable uploads**. Staging upload is single-shot; a 200 MB file dropping mid-upload restarts. Acceptable for v1's expected file sizes (typically < 10 MB each).
5. **Template versioning / sharing**. v1 is per-user, no history. If templates become standardized across the team, a v2 could add shared templates with role gates.

## Acceptance criteria (v1 "done")

- User can upload 20 files, write 1 prompt, and create a batch in one dialog.
- 3 sessions run concurrently; the rest queue and pick up as slots free.
- A failed child does not stop the batch; "重试失败" reruns failures.
- Batch list and detail views show accurate live progress (poll-driven, ≤ 5 s lag).
- Per-user prompt templates are CRUD-able and selectable in the create dialog.
- Clicking a child in the batch detail opens its real session in the conversation view.
- All backend tests + frontend stores tests pass; the Playwright smoke spec passes against a real OpenCode + MCP setup.
