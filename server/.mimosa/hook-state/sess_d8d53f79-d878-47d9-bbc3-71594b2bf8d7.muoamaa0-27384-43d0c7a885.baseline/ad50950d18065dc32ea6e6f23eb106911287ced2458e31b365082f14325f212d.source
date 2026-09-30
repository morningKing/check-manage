"""AI 批任务（batches + sessions 批列）。"""

AI_CHAT_BATCHES_DDL = """
CREATE TABLE IF NOT EXISTS ai_chat_batches (
  id          VARCHAR(100) PRIMARY KEY,
  user_id     VARCHAR(100) NOT NULL REFERENCES users(id),
  name        TEXT NOT NULL,
  prompt      TEXT NOT NULL,
  template_id VARCHAR(100) NULL REFERENCES ai_chat_prompt_templates(id) ON DELETE SET NULL,
  agent       TEXT,
  model       TEXT,
  provision_repo TEXT,
  provision_ref  TEXT,
  status      TEXT NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','running','paused','completed','partial','failed')),
  total       INT  NOT NULL DEFAULT 0,
  done        INT  NOT NULL DEFAULT 0,
  failed      INT  NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at  TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_chat_batches_user_created
  ON ai_chat_batches(user_id, created_at DESC);
-- Idempotent upgrade: add `agent`/`model` to DBs created before they joined CREATE above.
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS agent TEXT;
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS model TEXT;
-- Per-batch workspace provisioning: clone an agent/skill repo into each child's
-- .opencode/ before its session starts (so project-level agents are usable).
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS provision_repo TEXT;
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS provision_ref TEXT;
-- Optional completion callback for the open API (/api/v1/ai-batches): POSTed
-- to callback_url, HMAC-signed with callback_secret, when the batch reaches a
-- terminal status. NULL callback_url means "no callback, poll instead".
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS callback_url TEXT;
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS callback_secret TEXT;
-- 'paused' joined the status vocabulary later (批任务「暂停」，非终态、可
-- resume)。老库带的是没有它的 CHECK 约束 —— 幂等地替换（与上面 CREATE 里
-- 的定义保持一致；重复执行只是无谓地重建一次约束）。
ALTER TABLE ai_chat_batches DROP CONSTRAINT IF EXISTS ai_chat_batches_status_check;
ALTER TABLE ai_chat_batches ADD CONSTRAINT ai_chat_batches_status_check
  CHECK (status IN ('pending','running','paused','completed','partial','failed'));
"""

AI_CHAT_SESSIONS_BATCH_COLUMNS_DDL = """
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS batch_id         VARCHAR(100) NULL REFERENCES ai_chat_batches(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS batch_seq        INT  NULL,
  ADD COLUMN IF NOT EXISTS batch_input_file TEXT NULL,
  ADD COLUMN IF NOT EXISTS error_message         TEXT NULL,
  ADD COLUMN IF NOT EXISTS last_message_preview  TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_batch
  ON ai_chat_sessions(batch_id, batch_seq);
ALTER TABLE ai_chat_sessions
  ALTER COLUMN workspace_path DROP NOT NULL,
  ALTER COLUMN session_token DROP NOT NULL,
  ALTER COLUMN token_expires_at DROP NOT NULL;
"""
