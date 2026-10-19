# Batch Session Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make internal batch-task child sessions behave like ordinary AI chat sessions and allow safe human intervention in the same conversation after an automatic run fails or deviates.

**Architecture:** Keep `BatchWorker` authoritative for automatic and manually queued child turns, but move both paths onto shared session preparation and prompt-building helpers. Use the existing ordinary conversation renderer/SSE for the browser, while worker persistence remains the fallback when no browser is connected; atomic child-turn state transitions prevent worker/manual races.

**Tech Stack:** Flask/Python, PostgreSQL/psycopg2, OpenCode REST/SSE, Vue 3/TypeScript/Pinia, Vitest, Pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-11-batch-session-parity-design.md`

## Global Constraints

- `dynamic_bp` remains the last registered Flask blueprint.
- Batch children remain grouped under their parent batch and retain batch progress/retry/append/delete behavior.
- Worker persistence must work when no browser is open.
- External `/v1/ai-sessions` response and polling contract remain unchanged.
- Every user-facing change updates the relevant `docs/user-guide/` documentation.
- Frontend/full-stack changes require live Playwright verification and a screenshot under `.playwright-mcp/`.
- SSE changes require verification through `server/proxy.py`, not only Vite.

## File Map

- `server/utils/session_prompt.py`: shared prompt augmentation for ordinary and worker turns.
- `server/utils/session_workspace.py`: shared per-session workspace/config initialization; preserve existing workspace helper compatibility.
- `server/routes/ai_chat.py`: ordinary send/SSE paths and batch-child interaction guards.
- `server/utils/batch_engine.py`: worker turn claiming, continuation execution, and worker persistence.
- `server/utils/batch_repo.py`: atomic child continuation/reexecute/cancel state transitions.
- `server/routes/ai_chat_batches.py`: authenticated batch-child continuation and child lifecycle endpoints.
- `src/stores/aiChat.ts`: session opening, SSE, sending, and message reload behavior.
- `src/stores/aiChatBatches.ts`: batch polling and complete terminal-state handling.
- `src/views/ai-chat/AiChatView.vue`: child selection, composer gating, status labels, and direct URL opening.
- `src/api/aiChatBatches.ts`: continuation API client.
- `server/tests/`, `src/**/__tests__/`, `e2e/`: regression and parity coverage.
- `docs/user-guide/ai/assistant.md` and the matching batch-task guide: user-facing intervention workflow.

---

### Task 1: Add failing backend parity tests and shared prompt contract

**Files:**
- Create: `server/tests/test_session_prompt.py`
- Modify: `server/tests/test_ai_chat_directive.py`
- Modify: `server/tests/test_batch_engine.py`

**Interfaces:**
- Produce `build_session_prompt(*, content: str, workspace_path: str, attachments: list[str], agent_mentions: list[dict], user_id: str, role: str | None) -> tuple[str, list[dict]]` in `server/utils/session_prompt.py`.
- The returned tuple is `(agent_prompt, stored_parts)`; `stored_parts` contains the original user text/file chips and never the injected system text.

- [ ] **Step 1: Write failing tests**

  Test that ordinary and batch inputs produce the same augmented prompt for text attachments, binary attachments, `@` file mentions, memory, and export-intent fallback. Test that stored parts retain raw user content and file metadata only.

- [ ] **Step 2: Run the focused tests**

  Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_session_prompt.py tests/test_ai_chat_directive.py -v`

  Expected: FAIL because the shared helper does not exist.

- [ ] **Step 3: Implement the shared prompt helper**

  Move/reuse the existing logic from `server/routes/ai_chat.py` without changing ordinary-session output. Keep memory lookup and export handling injectable or patchable in tests. Do not include batch-only file hints in this helper; the worker adds those after shared augmentation when necessary.

- [ ] **Step 4: Route ordinary send through the helper**

  Replace the duplicated prompt assembly in `send_message()` with `build_session_prompt()`, preserving message IDs, model/agent overrides, and existing error behavior.

- [ ] **Step 5: Run tests**

  Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_session_prompt.py tests/test_ai_chat_directive.py tests/test_routes_ai_chat.py -v`

  Expected: PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add server/utils/session_prompt.py server/routes/ai_chat.py server/tests/test_session_prompt.py server/tests/test_ai_chat_directive.py server/tests/test_routes_ai_chat.py
  git commit -m "refactor: share AI session prompt preparation"
  ```

### Task 2: Unify worker workspace and OpenCode session preparation

**Files:**
- Create: `server/utils/session_workspace.py`
- Modify: `server/utils/workspace.py`
- Modify: `server/routes/ai_chat.py`
- Modify: `server/utils/batch_engine.py`
- Test: `server/tests/test_workspace.py`
- Test: `server/tests/test_batch_engine.py`

**Interfaces:**
- Produce `prepare_interactive_session_workspace(user_id: str, session_id: str, *, staged_inputs: str | list[str] | None, mcp_name: str, mcp_url: str, token: str, agent: str | None = None, model: str | None = None) -> str`.
- The helper creates the session workspace, copies staged inputs, and writes the same OpenCode configuration used by ordinary sessions.

- [ ] **Step 1: Add failing tests**

  Assert that a worker-prepared batch workspace contains `uploads/`, `opencode.json`, and the session-specific MCP URL/token. Assert that ordinary session creation still writes equivalent configuration.

- [ ] **Step 2: Run focused tests**

  Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace.py tests/test_batch_engine.py -k "workspace or mcp" -v`

  Expected: FAIL for worker workspace configuration.

- [ ] **Step 3: Implement the shared workspace preparation**

  Preserve path traversal protections and the existing string/list staged-input behavior. Generate or reuse the opaque session token through the existing token helper and persist token fields before the worker dispatches.

- [ ] **Step 4: Route both creation paths through the helper**

  Update ordinary creation and `_prepare_workspace()`/`_run_one()` so batch children receive the same MCP configuration without changing external API response fields.

- [ ] **Step 5: Run backend workspace and batch tests**

  Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace.py tests/test_batch_engine.py tests/test_routes_ai_chat.py -v`

  Expected: PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add server/utils/session_workspace.py server/utils/workspace.py server/routes/ai_chat.py server/utils/batch_engine.py server/tests/test_workspace.py server/tests/test_batch_engine.py
  git commit -m "feat: prepare batch sessions like interactive sessions"
  ```

### Task 3: Add safe manual continuation for child sessions

**Files:**
- Modify: `server/utils/batch_repo.py`
- Modify: `server/routes/ai_chat_batches.py`
- Modify: `server/utils/batch_engine.py`
- Modify: `server/routes/ai_chat.py`
- Create or modify: `server/tests/test_batch_continuation.py`
- Modify: `server/tests/test_batch_routes.py`

**Interfaces:**
- Produce `continue_child(batch_id: str, session_id: str, user_id: str, content: str, attachments: list[str], agent: str | None, model: str | None) -> dict` in the repository/service layer.
- Produce `POST /ai/chat/batches/<batch_id>/sessions/<session_id>/continue`, returning `202` with the stored message id and child status.
- The worker consumes continuation rows through the same `_run_one()` path and preserves `opencode_session_id`, workspace, and message history.

- [ ] **Step 1: Write failing repository tests**

  Cover terminal child continuation, rejection while automatic execution is running, ownership mismatch, parent counter/status recomputation, and atomic compare-and-set behavior under two continuation attempts.

- [ ] **Step 2: Run focused repository tests**

  Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_continuation.py tests/test_batch_routes.py -v`

  Expected: FAIL because the continuation endpoint/state transition is missing.

- [ ] **Step 3: Implement the atomic transition**

  Use one conditional SQL update keyed by child id, parent batch id, owner id, and terminal status. Insert the user message in the same transaction, set `continue_prompt`, clear the previous error, set `status='pending'`, and recompute the parent batch state/counters.

- [ ] **Step 4: Implement worker continuation**

  Detect continuation rows with an existing OpenCode session and `continue_prompt`, reuse the workspace/session, run the shared prompt builder, and persist the new turn. Keep the existing one-attempt OpenCode recovery path.

- [ ] **Step 5: Add the route and API authorization**

  Scope the endpoint to the authenticated batch owner, apply the same write permission used by ordinary chat, validate attachments with existing workspace-safe helpers, and return stable error codes for non-terminal/racing children.

- [ ] **Step 6: Run backend tests**

  Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_continuation.py tests/test_batch_routes.py tests/test_batch_engine.py tests/test_routes_ai_chat.py -v`

  Expected: PASS.

- [ ] **Step 7: Commit**

  ```bash
  git add server/utils/batch_repo.py server/routes/ai_chat_batches.py server/utils/batch_engine.py server/routes/ai_chat.py server/tests/test_batch_continuation.py server/tests/test_batch_routes.py
  git commit -m "feat: allow manual continuation of batch sessions"
  ```

### Task 4: Make batch child frontend interaction match ordinary sessions

**Files:**
- Modify: `src/api/aiChatBatches.ts`
- Modify: `src/stores/aiChat.ts`
- Modify: `src/stores/aiChatBatches.ts`
- Modify: `src/views/ai-chat/AiChatView.vue`
- Modify: `src/types/aiChatBatch.ts`
- Test: `src/stores/__tests__/aiChat.test.ts`
- Test: `src/stores/__tests__/aiChatBatches.test.ts`
- Create or modify: `src/views/ai-chat/__tests__/AiChatView.test.ts`

**Interfaces:**
- Add `continueBatchChild(batchId: string, sessionId: string, body: { content: string; attachments?: string[]; agent?: string; model?: string })` to `src/api/aiChatBatches.ts`.
- Expose a batch-child predicate and continuation action from the stores so the view does not infer child status from unrelated arrays.

- [ ] **Step 1: Write failing frontend tests**

  Test that batch children do not expose the composer while automatic execution is running, do expose it after `completed`/`failed`/`cancelled`, send through the batch continuation API, and open through the ordinary SSE path for intervention turns. Test that `partial` and `cancelled` stop polling.

- [ ] **Step 2: Run focused tests**

  Run: `npm run test -- src/stores/__tests__/aiChat.test.ts src/stores/__tests__/aiChatBatches.test.ts src/views/ai-chat/__tests__/AiChatView.test.ts`

  Expected: FAIL against current polling-only behavior.

- [ ] **Step 3: Implement API/store continuation flow**

  Add the API method, store action, optimistic status transition, reload logic, and SSE lifecycle handoff. Keep worker-persisted messages authoritative on reload and avoid duplicate assistant messages.

- [ ] **Step 4: Update the view**

  Resolve batch children for direct URL navigation, use complete status labels, disable ordinary controls during automatic execution, and route terminal-child sends through `continueBatchChild()` while preserving the shared renderer and file/artifact panels.

- [ ] **Step 5: Run frontend tests and type check**

  Run: `npm run test -- src/stores/__tests__/aiChat.test.ts src/stores/__tests__/aiChatBatches.test.ts src/views/ai-chat/__tests__/AiChatView.test.ts && npm run build`

  Expected: PASS with no TypeScript errors.

- [ ] **Step 6: Commit**

  ```bash
  git add src/api/aiChatBatches.ts src/stores/aiChat.ts src/stores/aiChatBatches.ts src/views/ai-chat/AiChatView.vue src/types/aiChatBatch.ts src/stores/__tests__ src/views/ai-chat/__tests__
  git commit -m "feat: enable interactive batch child sessions"
  ```

### Task 5: Make reexecute and terminal cleanup deterministic

**Files:**
- Modify: `server/utils/batch_repo.py`
- Modify: `server/utils/batch_engine.py`
- Modify: `server/utils/workspace.py`
- Modify: `server/routes/ai_chat_batches.py`
- Test: `server/tests/test_batch_engine.py`
- Test: `server/tests/test_open_api_batches_sessions.py`

- [ ] **Step 1: Write failing cleanup tests**

  Create a child workspace containing generated files, reexecute it, and assert the old workspace contents are removed before the new run. Test that cancel and failed completion leave a recoverable child state and that deleting a parent still removes child resources.

- [ ] **Step 2: Run focused tests**

  Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_engine.py tests/test_open_api_batches_sessions.py -k "reexecute or cancel or cleanup" -v`

  Expected: FAIL for stale workspace behavior or missing assertions.

- [ ] **Step 3: Implement cleanup and status recomputation**

  Add an idempotent workspace cleanup helper, call it only for reexecute/reset—not manual continuation—and ensure parent counters/status are recomputed after cancellation and continuation.

- [ ] **Step 4: Run backend regression tests**

  Run: `npm run test:server`

  Expected: PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add server/utils/batch_repo.py server/utils/batch_engine.py server/utils/workspace.py server/routes/ai_chat_batches.py server/tests/test_batch_engine.py server/tests/test_open_api_batches_sessions.py
  git commit -m "fix: cleanly reset batch child executions"
  ```

### Task 6: Document and verify the end-to-end intervention flow

**Files:**
- Modify: `docs/user-guide/ai/assistant.md`
- Modify: the existing batch-task user guide under `docs/user-guide/ai/` or `docs/user-guide/integration/`
- Create: `e2e/ai-chat-batch-intervention.spec.ts`
- Create: `.playwright-mcp/batch-intervention.png` (generated evidence, not hand-authored)
- Modify: `server/proxy.py` if the final SSE route allowlist needs adjustment

- [ ] **Step 1: Add the Playwright scenario**

  Log in as `admin`, create a batch that reaches a failed child, open the child, send a corrective message, assert the assistant response appears in the same conversation, and verify the persisted child message/workspace state through the UI/API-backed result.

- [ ] **Step 2: Run the frontend and backend services**

  Run: `npm run dev:all`

- [ ] **Step 3: Run the Vite-backed Playwright flow**

  Run: `npx playwright test e2e/ai-chat-batch-intervention.spec.ts`

  Expected: PASS and screenshot written to `.playwright-mcp/batch-intervention.png`.

- [ ] **Step 4: Verify production proxy streaming**

  Run: `npm run build`, then start `server/proxy.py`, open the intervention flow through `http://localhost:8080`, and confirm SSE frames arrive incrementally. Update `_is_sse()` only if the final route shape is not already covered.

- [ ] **Step 5: Update user documentation**

  Document automatic execution, failed-run intervention, continuing the same child session, cancellation, reexecute, and the distinction between manual continuation and clean reexecute.

- [ ] **Step 6: Run final verification**

  Run: `npm run test:all && npm run build && npx playwright test e2e/ai-chat-batch-intervention.spec.ts`

- [ ] **Step 7: Commit**

  ```bash
  git add docs/user-guide/ai/assistant.md docs/user-guide e2e/ai-chat-batch-intervention.spec.ts .playwright-mcp/batch-intervention.png server/proxy.py
  git commit -m "docs: document batch session intervention"
  ```

## Self-review

- Shared prompt, workspace/MCP setup, worker continuation, frontend interaction, cleanup, terminal states, documentation, and Playwright/proxy verification each have explicit tasks.
- No task changes the external `/v1/ai-sessions` public contract.
- The only intentional dual persistence path is Worker persistence plus browser rendering; tests must verify no duplicate assistant messages.
- Manual continuation preserves workspace/OpenCode context; reexecute alone performs destructive reset.
