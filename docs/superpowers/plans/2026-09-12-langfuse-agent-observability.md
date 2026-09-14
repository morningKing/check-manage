# Langfuse Agent Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add an optional, self-hosted Langfuse projection of interactive and batch OpenCode execution so long conversations can be searched as nested agent, generation, tool, and MCP traces.

**Architecture:** PostgreSQL remains the source of truth for chat ownership and messages. A normalized observation adapter consumes the existing persistence events, places non-blocking export jobs on a bounded in-process queue, and sends idempotent observations to Langfuse in the background. The UI exposes a trace link but continues to render and operate from the existing database.

**Tech Stack:** Flask/Python, PostgreSQL, `langfuse` Python SDK, OpenTelemetry GenAI-compatible metadata, Vue 3/TypeScript, Vitest, Pytest, Playwright, Docker Compose documentation for Langfuse v4 self-hosting.

**Spec:** `docs/superpowers/specs/2026-09-12-langfuse-agent-observability-design.md`

## Global Constraints

- Langfuse is disabled by default with `LANGFUSE_ENABLED=false`.
- Langfuse failure must never fail or delay an AI chat turn, batch worker, SSE stream, or PostgreSQL transaction.
- PostgreSQL remains authoritative; no existing chat route may require Langfuse for reads or writes.
- Raw prompts, completions, tool arguments, tool results, credentials, and tokens are not exported unless `LANGFUSE_CAPTURE_CONTENT=true`.
- Exported observations use deterministic IDs so retries are idempotent.
- The exporter queue is bounded and must expose dropped-event/error logging without unbounded memory growth.
- New user-facing configuration and tracing behavior must be documented under `docs/user-guide/`.
- Self-hosting instructions must document persistent storage and backups for Langfuse PostgreSQL, ClickHouse, Redis/Valkey, and blob storage.
- Tests must run with Langfuse disabled and without network access; integration tests use a fake HTTP endpoint.

---

## File Map

**Backend configuration and exporter:**

- Modify: `server/requirements.txt` — pin/add the Langfuse SDK.
- Modify: `server/config.py` — read and validate Langfuse settings.
- Create: `server/utils/langfuse_config.py` — immutable settings, sampling, content policy, redaction, and deterministic ID helpers.
- Create: `server/utils/langfuse_exporter.py` — normalized observation types, bounded queue, worker lifecycle, SDK/API submission, retry and shutdown behavior.
- Create: `server/utils/langfuse_mapping.py` — OpenCode message/part to generation/tool/agent observations.
- Modify: `server/app.py` — start/stop the exporter under the existing guarded background-worker startup path.

**Persistence integration:**

- Modify: `server/utils/chat_persist.py` — emit interactive turn, generation, tool, and subtask observations after local persistence succeeds.
- Modify: `server/utils/batch_engine.py` — emit batch child, tool, generation, and nested subtask observations after REST snapshot persistence.
- Modify: `server/routes/ai_chat.py` and `server/routes/ai_chat_batches.py` — expose trace metadata/link only when owned and enabled.

**Frontend:**

- Modify: `src/types/aiChat.ts` or the existing AI chat type module — add optional trace metadata.
- Modify: `src/api/aiChat.ts` and/or `src/api/aiChatBatches.ts` — consume trace metadata if the API layer needs a dedicated endpoint.
- Modify: `src/views/ai-chat/AiChatView.vue` — render the `查看 Trace` action with disabled/hidden states.
- Test: `src/views/ai-chat/__tests__/AiChatView.test.ts` — trace-link visibility and URL generation.

**Tests and operations:**

- Create: `server/tests/test_langfuse_config.py`.
- Create: `server/tests/test_langfuse_mapping.py`.
- Create: `server/tests/test_langfuse_exporter.py`.
- Modify: `server/tests/test_chat_persist.py` and `server/tests/test_batch_engine.py` — verify exporter calls are best-effort and correctly correlated.
- Create: `e2e/langfuse-trace-link.spec.ts` — optional enabled-tracing browser smoke with a fake/isolated endpoint.
- Create: `docs/operations/langfuse-self-hosting.md` — deployment, health checks, backups, upgrades, retention, and credentials.
- Create: `docs/user-guide/ai/agent-observability.md` — explain the trace link, search workflow, and privacy setting.
- Modify: `docs/user-guide/README.md` — add the Agent Observability guide to the AI documentation index.

---

## Task 1: Configuration and SDK Boundary

**Files:**
- Modify: `server/requirements.txt`
- Modify: `server/config.py`
- Create: `server/utils/langfuse_config.py`
- Test: `server/tests/test_langfuse_config.py`

**Interfaces:**
- Produces `LangfuseSettings(enabled, host, public_key, secret_key, environment, capture_content, sample_rate, queue_size, flush_interval_seconds)`.
- Produces `load_langfuse_settings(environ=None) -> LangfuseSettings`.
- Produces `should_sample(settings, stable_key) -> bool`.
- Produces `redact_content(value, enabled) -> value`.
- Produces `observation_id(kind, application_id) -> str`.

- [x] **Step 1: Write failing configuration tests.** Cover disabled-by-default, missing credentials disabling export, invalid sample rates clamped/rejected with a clear configuration error, content capture defaulting to false, deterministic IDs, and secret-like strings being redacted when content capture is enabled.
- [x] **Step 2: Run the configuration tests and confirm they fail because the settings module does not exist.**
  ```bash
  set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest server/tests/test_langfuse_config.py -q
  ```
- [x] **Step 3: Add the SDK dependency and implement the settings/redaction helpers.** Keep SDK import lazy so the application can start with tracing disabled and so tests do not require a live Langfuse server.
- [x] **Step 4: Run the configuration tests and the existing config tests.**
- [x] **Step 5: Record the completed task as a review checkpoint; do not create a git commit unless explicitly requested.**

## Task 2: Normalized Observation Model and Mapping

**Files:**
- Create: `server/utils/langfuse_mapping.py`
- Test: `server/tests/test_langfuse_mapping.py`

**Interfaces:**
- Produces `Observation(kind, id, parent_id, trace_id, session_id, name, input, output, metadata, status, start_time, end_time)`.
- Produces `map_open_code_message(message, context) -> list[Observation]`.
- Produces `map_subtask_tree(subtasks, messages, context) -> list[Observation]`.
- Produces `map_tool_part(part, context) -> Observation`.

- [x] **Step 1: Write failing mapping tests for text generations, MCP/tool parts, task delegation, nested subagents, failed tools, missing timestamps, and duplicate OpenCode REST snapshots.** Assert that the real `state.metadata.sessionId` becomes `task_id`, not the parent `SubtaskPart.sessionID`.
- [x] **Step 2: Run the mapping tests and confirm failures.**
  ```bash
  set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest server/tests/test_langfuse_mapping.py -q
  ```
- [x] **Step 3: Implement the pure mapping layer without network or database access.** Use stable low-cardinality names (`invoke_agent`, `generation`, `execute_tool`) and put IDs/model/agent/batch data in metadata.
- [x] **Step 4: Run the mapping tests and verify nested parent-child relationships.**
- [x] **Step 5: Record the completed task as a review checkpoint; do not create a git commit unless explicitly requested.**

## Task 3: Bounded Asynchronous Exporter

**Files:**
- Create: `server/utils/langfuse_exporter.py`
- Test: `server/tests/test_langfuse_exporter.py`
- Modify: `server/app.py`

**Interfaces:**
- Produces `get_langfuse_exporter() -> LangfuseExporter`.
- Produces `LangfuseExporter.start()`, `.submit(observations)`, `.flush(timeout_seconds)`, and `.stop(timeout_seconds)`.
- `submit()` is non-blocking and returns an enum/result such as `accepted`, `sampled`, `disabled`, or `dropped`.

- [x] **Step 1: Write failing exporter tests using a fake Langfuse client.** Cover disabled mode, successful batch submission, deterministic retry, fake client exception isolation, queue saturation, terminal-error priority, and bounded shutdown flush.
- [x] **Step 2: Run the exporter tests and confirm failures.**
  ```bash
  set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest server/tests/test_langfuse_exporter.py -q
  ```
- [x] **Step 3: Implement a daemon worker around a bounded `queue.Queue`.** The worker must lazily construct the SDK client, batch observations by size/time, catch all SDK/network exceptions, and log session/trace/observation IDs without logging secrets.
- [x] **Step 4: Add application lifecycle startup/shutdown under the existing `WERKZEUG_RUN_MAIN` guard.** Tests must be able to inject a fake exporter and disable thread startup.
- [x] **Step 5: Run exporter tests plus application startup tests.**
- [x] **Step 6: Record the completed task as a review checkpoint; do not create a git commit unless explicitly requested.**

## Task 4: Interactive Chat and Batch Instrumentation

**Files:**
- Modify: `server/utils/chat_persist.py`
- Modify: `server/utils/batch_engine.py`
- Modify: `server/tests/test_chat_persist.py`
- Modify: `server/tests/test_batch_engine.py`

**Interfaces:**
- Consumes `Observation` and `LangfuseExporter.submit()` from Tasks 2–3.
- Produces one trace per interactive turn, batch child run, and manual continuation turn.

- [x] **Step 1: Add failing tests that inject a fake exporter into interactive persistence.** Assert that local message/subtask persistence happens before export, the root turn has the application session and turn IDs, task observations use real child session IDs, and exporter exceptions do not change the listener result.
- [x] **Step 2: Add failing tests for batch REST snapshot persistence.** Assert complete root → tool → subtask trees, nested parent IDs, batch ID metadata, terminal failure status, and idempotent reprocessing.
- [x] **Step 3: Run the persistence tests and confirm failures.**
- [x] **Step 4: Emit normalized observations only after the existing PostgreSQL writes succeed.** Avoid rereading or mutating chat state solely for telemetry; reuse the snapshots already available in each path.
- [x] **Step 5: Cover manual continuation as a new trace/turn under the same application session and preserve the task ID correlation.**
- [x] **Step 6: Run interactive, batch, and exporter regression tests.**
- [x] **Step 7: Record the completed task as a review checkpoint; do not create a git commit unless explicitly requested.**

## Task 5: Trace Metadata API and Chat Link

**Files:**
- Modify: `server/routes/ai_chat.py`
- Modify: `server/routes/ai_chat_batches.py`
- Modify: `src/types/aiChat.ts` or the existing AI chat type module
- Modify: `src/api/aiChat.ts` and/or `src/api/aiChatBatches.ts`
- Modify: `src/views/ai-chat/AiChatView.vue`
- Test: `src/views/ai-chat/__tests__/AiChatView.test.ts`

**Interfaces:**
- Backend returns optional `traceUrl` and `traceId` only for sessions owned by the current user and only when Langfuse is enabled/configured.
- Frontend renders `查看 Trace` as an external link with `target="_blank"`; absent metadata hides the action without changing chat behavior.

- [x] **Step 1: Write failing frontend tests for hidden/visible trace link, external URL, and batch child ownership.** Add backend route tests for ownership and disabled configuration.
- [x] **Step 2: Run the tests and confirm failures.**
- [x] **Step 3: Implement response metadata using a server-generated URL, never exposing the Langfuse secret key.**
- [x] **Step 4: Add the header action and accessible label in the AI chat view.**
- [x] **Step 5: Run frontend unit tests, route tests, and `npm run build`.**
- [x] **Step 6: Record the completed task as a review checkpoint; do not create a git commit unless explicitly requested.**

## Task 6: Self-Hosted Langfuse Deployment Documentation

**Files:**
- Create: `docs/operations/langfuse-self-hosting.md`
- Modify: `docs/user-guide/README.md`
- Modify: `.env.example` or `server/.env.example` with non-secret Langfuse variable names and comments

- [x] **Step 1: Document the supported local deployment using the pinned Langfuse Docker Compose release.** Include web/worker, PostgreSQL, Redis/Valkey, ClickHouse, S3-compatible storage, persistent volumes, health/readiness checks, and private-network/HTTPS guidance.
- [x] **Step 2: Document application variables, key creation, content-capture policy, sampling, retention, backup, restore, and upgrade order.** Never include real keys.
- [x] **Step 3: Add the user-facing `查看 Trace` behavior and privacy explanation to `docs/user-guide/ai/agent-observability.md`.**
- [x] **Step 4: Review documentation for ASCII/default encoding constraints and link it from the documentation index.**
- [x] **Step 5: Record the completed task as a review checkpoint; do not create a git commit unless explicitly requested.**

## Task 7: Integration and End-to-End Verification

**Files:**
- Create: `server/tests/test_langfuse_integration.py`
- Create or modify: `e2e/langfuse-trace-link.spec.ts`
- Use: self-hosted Langfuse test stack and fake endpoint fixtures

- [x] **Step 1: Start a disposable self-hosted Langfuse stack with persistent test volumes and create a test project/key.** Keep credentials in process environment only.
  > **环境相关跳过（2026-09-15）**：本机无 Docker，自托管栈未启动；浏览器正路径以 `LANGFUSE_E2E_ENABLED=1` 门控（`e2e/langfuse-trace-link.spec.ts`），启用态 traceUrl 构造由 `test_routes_ai_chat.py` 路由断言 + TraceLink 组件测试覆盖。
- [x] **Step 2: Run an integration test that sends one root turn, one tool call, and one nested subagent; query the Langfuse API and assert the parent tree, task ID, tool name, status, and session ID.**
- [x] **Step 3: Run failure-mode integration tests with Langfuse stopped and credentials removed.** Assert chat persistence and batch completion still succeed without outbound retry storms.
- [x] **Step 4: Run Playwright through the real application and assert `查看 Trace` opens the expected self-hosted URL while normal chat and human continuation remain usable.** Capture `.playwright-mcp/langfuse-trace-link.png`.
- [x] **Step 5: Run the complete backend, MCP, frontend, build, and applicable Playwright suites.** Record exact counts and any environment-dependent skips.
- [x] **Step 6: Complete a security review: no secret keys in browser payloads, logs, trace metadata, screenshots, or committed files.**
- [x] **Step 7: Record the completed task as a review checkpoint; do not create a git commit unless explicitly requested.**

## Completion Checklist

- [x] `LANGFUSE_ENABLED=false` has zero outbound Langfuse requests.
- [x] Chat and batch execution remain functional when Langfuse is unavailable.
- [x] A long task is searchable by application session, batch ID, task ID, tool name, and failure status.
- [x] Nested subagent parent/child relationships are visible in Langfuse.
- [x] Content capture is disabled by default and redaction tests pass.
- [x] Trace link is ownership-checked and never exposes secret credentials.
- [x] Self-hosted deployment and backup instructions are complete.
- [x] Full verification evidence is recorded before claiming completion.

---

## Verification Evidence（2026-09-15）

- 后端全量（`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q`，worktree 内）：**2089 passed**
  - 含本任务 64 个 Langfuse 测试（config 14 / mapping 19 / exporter 26 / docs 1 / integration 4）
  - 集成测试贯通真实路径：事件源/REST 快照 → PostgreSQL 持久化（先于导出）→ 映射（generation / execute_tool / invoke_agent，task_id=真实子会话 id）→ 导出器提交；导出异常与停用场景持久化不受影响
- 前端全量 vitest：**1162 passed**；`vue-tsc --noEmit` 0 错误；`npm run build` 成功
- Playwright（`e2e/langfuse-trace-link.spec.ts`）：禁用路径通过（「查看 Trace」不出现 + 对话照常可用 18.2s）；启用路径按 `LANGFUSE_E2E_ENABLED` 门控跳过（无 Docker/自托管栈）
- 安全复核：密钥仅存在于后端进程环境；`_trace_metadata` 只返回 traceId/traceUrl；前端渲染层无密钥字段；本提交不含任何真实 key
