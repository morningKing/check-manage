# Batch Session Parity Design

## Goal

Make internal batch-task child sessions behave like ordinary AI chat sessions after the worker starts them, so a user can inspect a failed or deviating run and intervene in the same conversation without losing workspace, OpenCode context, or message history.

The external `/v1/ai-sessions` polling API remains unchanged in this work. It has no interactive browser surface and is a separate public contract.

## User-visible behavior

- A batch child shows the same conversation renderer, files, artifacts, tool calls, subtasks, and context controls as an ordinary session.
- While automatic execution is active, the child is viewable and can be stopped, but a second user turn cannot race the worker.
- After `completed`, `failed`, or `cancelled`, the user can continue the same child session manually.
- Manual continuation reuses the child workspace and OpenCode session, appends messages to the same history, and returns to the running state until the turn finishes.
- Reexecute remains a clean restart operation; it removes stale execution state and workspace artifacts before starting over.
- Batch-level progress, retry-failed, append-file, cancellation, and deletion remain available.
- `partial` and `cancelled` are treated as terminal batch states by the frontend.

## Backend design

### Shared session preparation

Extract shared helpers for ordinary and batch sessions covering workspace initialization, per-session OpenCode configuration, MCP token setup, prompt construction, memory injection, attachment handling, `@` file expansion, and export fallback behavior. The batch worker must use the same preparation path as ordinary chat wherever the operation is applicable.

### Execution ownership

The batch worker remains authoritative for the initial batch turn and for queued manual continuation turns. The continuation endpoint atomically transitions a terminal child to a pending/running continuation state and stores the user message before the worker claims it. A running child rejects a second continuation unless the existing ordinary-session queue semantics can be implemented safely.

Worker persistence remains necessary when no browser is open. Browser SSE must render the same events without duplicating worker-persisted assistant messages. A session/turn guard prevents worker execution and manual continuation from claiming the same turn concurrently.

### Lifecycle and cleanup

Reuse the existing OpenCode session and workspace for manual continuation. Keep ordinary close/reopen/clear/delete semantics where they make sense for a child, while retaining batch-specific reexecute and cancel actions. Reexecute must clear the old workspace and reset OpenCode/session message state before enqueueing a fresh run.

### Terminal states

Backend and frontend state handling must consistently recognize `completed`, `partial`, `failed`, and `cancelled` as terminal where applicable. A manual continuation moves the selected child and its parent batch back to an active/running state with counters recomputed consistently.

## Frontend design

- Open batch children with the ordinary session interaction path, including SSE for human-intervened turns.
- Keep Worker-backed initial progress visible and reload persisted messages as a safety net.
- Disable the composer and ordinary mutation controls while automatic execution is running; enable them after a terminal state.
- Route manual sends through the batch continuation path rather than directly bypassing worker ownership.
- Preserve batch status bar, batch agent/model display, child actions, file panels, artifact actions, and subtask rendering.
- Allow direct `/ai-chat?session=<batch-child-id>` navigation by resolving batch membership before opening the child.

## Failure handling

- If OpenCode dispatch fails, use the existing session recovery path and preserve the child id and conversation history.
- If the workspace or staged input is missing, mark the child failed with an actionable error and allow manual intervention when the OpenCode session can be recovered.
- Never expose partial assistant output as a completed external API result; this existing external contract remains unchanged.

## Verification

- Backend unit tests for shared prompt/workspace parity, worker continuation, atomic state transitions, recovery, cancel, reexecute cleanup, and terminal-state recomputation.
- Frontend unit tests for batch-child composer gating, SSE/polling handoff, terminal states, direct URL opening, and manual continuation.
- Playwright coverage for: create batch, wait for failure, open child, send a corrective message, observe streamed response, inspect persisted history/files, and reexecute after cleanup.
- Update the relevant user guide sections describing batch intervention and manual continuation.
