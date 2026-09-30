"""AI 提示词模板。"""

AI_CHAT_PROMPT_TEMPLATES_DDL = """
CREATE TABLE IF NOT EXISTS ai_chat_prompt_templates (
  id         VARCHAR(100) PRIMARY KEY,
  user_id    VARCHAR(100) NOT NULL REFERENCES users(id),
  name       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_chat_prompt_templates_user
  ON ai_chat_prompt_templates(user_id, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_template_user_name
  ON ai_chat_prompt_templates(user_id, name);
"""
