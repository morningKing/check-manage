"""AI 定时扫描。"""

AI_SCAN_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS ai_scan_tasks (
  id              VARCHAR(100) PRIMARY KEY,
  name            TEXT NOT NULL,
  enabled         BOOLEAN NOT NULL DEFAULT TRUE,
  owner_user_id   VARCHAR(100) NOT NULL REFERENCES users(id),
  collection      VARCHAR(200) NOT NULL,
  branch_id       VARCHAR(100) NOT NULL DEFAULT 'main',
  status_field    TEXT NOT NULL,
  pending_value   TEXT NOT NULL DEFAULT '',
  running_value   TEXT NOT NULL DEFAULT '处理中',
  done_value      TEXT NOT NULL DEFAULT '已处理',
  failed_value    TEXT NOT NULL DEFAULT '处理失败',
  extra_filter    JSONB NOT NULL DEFAULT '{}'::jsonb,
  context_fields  JSONB NOT NULL DEFAULT '{}'::jsonb,
  prompt_template TEXT NOT NULL,
  field_mapping   JSONB NOT NULL DEFAULT '[]'::jsonb,
  schedule_interval_minutes INT NOT NULL DEFAULT 15,
  max_records_per_scan      INT NOT NULL DEFAULT 20,
  agent           TEXT,
  last_run_at     TIMESTAMPTZ,
  last_scan_count INT DEFAULT 0,
  last_error      TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS scan_task_id     VARCHAR(100) NULL REFERENCES ai_scan_tasks(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_record_id VARCHAR(100) NULL;
CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_scan
  ON ai_chat_sessions(scan_task_id, source_record_id);
-- Same signal, stamped on the batch itself (not just its child sessions) so the
-- AI 助手 sidebar can group "AI 定时任务" batches separately from user-created
-- ones without joining to ai_chat_sessions. Lives here (not in
-- AI_CHAT_BATCHES_DDL, which runs before this table exists) because the FK
-- target ai_scan_tasks is only created a few statements above.
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS scan_task_id VARCHAR(100)
  REFERENCES ai_scan_tasks(id) ON DELETE SET NULL;
-- Idempotent upgrade: add `agent` to DBs created before it joined the CREATE above.
ALTER TABLE ai_scan_tasks ADD COLUMN IF NOT EXISTS agent TEXT;
-- Continue conversation: store the prompt for a "continue" operation so the
-- worker can pick it up and send it to the existing OpenCode session.
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS continue_prompt TEXT;
-- Standalone single-session open API (/v1/ai-sessions): a session with no
-- parent ai_chat_batches row. api_key_id mirrors ai_chat_batches.api_key_id's
-- isolation model; agent/model mirror ai_chat_batches' same-named columns
-- (a standalone session has no parent batch row to hold them). The initial
-- prompt to send reuses the continue_prompt column above rather than adding
-- a new one — batch_engine._run_one reads it on first claim and clears it
-- immediately, the same way the existing "continue" flow does.
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS api_key_id VARCHAR(100)
  REFERENCES api_keys(id) ON DELETE SET NULL;
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS agent TEXT;
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS model TEXT;
-- Optional file attachments for a standalone session (POST /v1/ai-sessions
-- body.files). JSONB array of {"name","path"} — same shape as
-- ai_chat_batches' per-child files, staged via the same POST
-- /v1/ai-batches/uploads endpoint (no dedicated upload endpoint for
-- sessions). NULL when the session has no attachments. batch_engine._run_one
-- copies every path into the session's uploads/ (see _prepare_workspace's
-- str|list[str] handling) instead of the single batch_input_file column,
-- since one session can carry more than one file.
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS input_files JSONB;
-- Cooperative-cancel flag for the external POST .../cancel endpoints (both
-- batch children and standalone sessions live in this one table). No CHECK
-- constraint on `status`, so the worker can write the literal 'cancelled'
-- terminal value straight into that column — see utils/batch_engine.py's
-- claim-time sweep + mid-run _await_finished check.
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE;
-- Cooperative-PAUSE flag (批任务「暂停」): same mechanism as cancel_requested
-- (set by the route, honored by the claim-time sweep + mid-run poll check in
-- batch_engine), but the worker lands the child on the NON-terminal 'paused'
-- status — no failed-counter change, batch shows paused, resume restarts it.
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS pause_requested BOOLEAN NOT NULL DEFAULT FALSE;
-- Automatic-retry budget for failed batch/api children (批任务自动重试).
-- Incremented by batch_engine when a retryable failure (stall / stuck tool /
-- network) re-enqueues the child; ProviderAuth-style errors never retry.
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS retry_count INT NOT NULL DEFAULT 0;
-- Admin session list index: covers ORDER BY created_at DESC with optional
-- status/source_type filters. Partial index on status keeps it small.
CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_admin_list
  ON ai_chat_sessions(status, created_at DESC);
"""
