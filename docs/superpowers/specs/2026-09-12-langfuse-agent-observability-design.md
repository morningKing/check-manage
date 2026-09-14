# Langfuse Agent Observability Design

**Status:** Draft for review

## Goal

Add self-hosted Langfuse observability for long-running AI conversations, including root turns, model generations, tool/MCP calls, and nested OpenCode subagents, so operators can search and inspect a specific action without manually scanning the chat transcript.

## Context

The application already persists interactive and batch conversations in PostgreSQL. `ai_chat_messages` stores typed assistant content, while `ai_chat_subtasks` and `ai_chat_subtask_messages` store the discovered OpenCode subagent tree and its messages. `chat_persist.py` handles interactive event streams and `batch_engine.py` persists batch conversations from OpenCode REST snapshots. The integration must reuse these facts instead of becoming a second source of truth.

Langfuse self-hosting is a separate operational service. The current project has no Docker Compose or Kubernetes deployment manifest, so the first deployment target is an independently managed Langfuse Docker Compose stack. Langfuse's documented architecture includes web and worker containers plus PostgreSQL, Redis/Valkey, ClickHouse, and S3-compatible blob storage.

## Scope

### In scope

- Self-hosted Langfuse connection configuration for the Flask backend.
- A best-effort, asynchronous exporter that never blocks or breaks AI chat when Langfuse is unavailable.
- Trace mapping for interactive sessions, batch children, manual continuation turns, model generations, tools/MCP calls, and nested subagents.
- Stable correlation fields: application session ID, turn ID, OpenCode message/part ID, task ID, parent task ID, batch ID, user ID, agent, model, status, and duration.
- Content capture controls with safe-by-default redaction/disablement.
- A deep link from the AI chat session to the corresponding Langfuse trace/session when tracing is enabled.
- Unit tests for mapping, queue failure isolation, configuration, and content policy.
- A documented self-host deployment and application configuration flow.

### Out of scope for the first release

- Replacing PostgreSQL chat persistence with Langfuse.
- Rebuilding Langfuse's trace explorer inside the application.
- Automatic instrumentation of arbitrary downstream libraries.
- LLM-as-a-judge evaluation, datasets, prompt management, alerts, or dashboards beyond what Langfuse provides out of the box.
- Sending existing historical conversations retroactively; a later backfill command may be added after the live path is stable.

## Architecture

```text
OpenCode events / REST snapshots
              |
       existing persistence
              |
   Langfuse event adapter
              |
       bounded async queue
              |
       Langfuse SDK/API
              |
  self-hosted Langfuse web/worker
```

The application remains authoritative for chat content and ownership. The exporter receives normalized observations from the existing persistence paths and submits them in batches. Export failures are logged with the application session/turn IDs and do not affect the OpenCode listener, worker, SSE stream, or database transaction.

### Trace model

- Langfuse `session_id`: the application `ai_chat_sessions.id`.
- Langfuse trace: one user turn, batch child execution, or manual continuation turn.
- Root observation: the root agent/turn, with user input and final output only when content capture is enabled.
- Generation observation: each OpenCode model response/turn.
- Tool observation: each tool call, including MCP calls, with tool name, call ID, status, duration, and optional arguments/result.
- Agent observation: each discovered subagent, keyed by the real OpenCode `state.metadata.sessionId` task ID.
- Parent relationship: root agent → task tool call → subagent; nested subagents retain `parent_subtask_id`.

### Correlation metadata

Every observation carries low-cardinality metadata where available:

```json
{
  "app_session_id": "...",
  "app_turn_id": "...",
  "opencode_session_id": "...",
  "task_id": "ses_...",
  "parent_task_id": "ses_...",
  "batch_id": "...",
  "agent": "general",
  "model": "...",
  "environment": "development|staging|production"
}
```

High-cardinality or sensitive data belongs in input/output fields only when explicitly enabled, not in observation names or tags.

## Configuration and privacy

The backend reads the following environment variables and remains disabled unless explicitly enabled:

- `LANGFUSE_ENABLED` — default `false`.
- `LANGFUSE_HOST` — self-hosted Langfuse base URL.
- `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` — credentials.
- `LANGFUSE_ENVIRONMENT` — default application environment.
- `LANGFUSE_CAPTURE_CONTENT` — default `false`; controls prompts, tool arguments/results, and completions.
- `LANGFUSE_SAMPLE_RATE` — default `1.0` when enabled.
- `LANGFUSE_QUEUE_SIZE` and `LANGFUSE_FLUSH_INTERVAL_SECONDS` — bounded exporter controls.

When content capture is disabled, the exporter sends event type, names, IDs, statuses, token/latency data, lengths, and safe summaries only. Credentials and raw authentication tokens must never be exported. The exporter must truncate oversized payloads and redact known secret patterns before sending optional content.

## Failure handling

- Langfuse SDK/network failure is caught and logged; it cannot fail a chat turn.
- The queue is bounded. On saturation, low-priority content events are dropped with a counter/log entry; terminal status/error events are retained where possible.
- Shutdown flush is best-effort with a short timeout.
- Duplicate exports use deterministic observation IDs derived from application IDs so retries are idempotent.
- Langfuse downtime does not block PostgreSQL persistence or OpenCode execution.

## User experience

The AI chat session header exposes `查看 Trace` only when a trace URL is available. The link opens the self-hosted Langfuse trace/session in a new tab. The existing chat UI remains the primary conversation and human-intervention surface.

## Testing

- Unit: normalize OpenCode message parts into generation/tool/agent observations.
- Unit: preserve task ID and parent task ID for nested subagents.
- Unit: content-disabled policy excludes prompt, tool arguments/results, and completion text.
- Unit: exporter failures, queue saturation, retry, and shutdown do not raise into chat paths.
- Integration: fake Langfuse endpoint receives a complete root → tool → subagent tree.
- Integration: disabled/missing credentials produce no outbound request and no chat error.
- E2E: a real long task with a subagent exposes a `查看 Trace` link and the exported trace contains the task ID and tool observations.
- Deployment smoke: self-hosted Langfuse health/readiness, ingestion, and UI login are verified separately from application tests.

## Operational requirements

- Langfuse deployment must have persistent volumes and backups for PostgreSQL, ClickHouse, and blob storage.
- Application and Langfuse must communicate over a private network or HTTPS.
- Langfuse retention and access control must be configured before enabling content capture in production.
- The application documents the minimum Docker Compose stack, environment variables, health checks, and upgrade/backup expectations.

## Alternatives considered

### Direct OpenTelemetry export only

This reduces vendor coupling but leaves the application without the immediate Agent Graph, long-session search, and tool-focused UI required by the use case. OpenTelemetry semantic conventions remain the compatibility layer underneath the Langfuse adapter.

### Langfuse as the only persistence layer

Rejected because the existing PostgreSQL records drive ownership, permissions, chat rendering, batch continuation, and audit behavior. Langfuse is an observability projection, not the application source of truth.

### Synchronous SDK calls in chat paths

Rejected because Langfuse network latency or downtime would directly affect long-running sessions and batch worker capacity.
