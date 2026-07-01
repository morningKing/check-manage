"""幂等迁移：客服实例表 + ai_chat_sessions 增列 + kefu-guest 只读角色。
可独立执行（python migrate_kefu.py）或被 init_db 调用。"""
from db import get_db

_SQL = """
CREATE TABLE IF NOT EXISTS kefu_instances (
  id               VARCHAR(100) PRIMARY KEY,
  slug             VARCHAR(100) NOT NULL UNIQUE,
  name             VARCHAR(200) NOT NULL,
  agent            TEXT,
  model            TEXT,
  system_prompt    TEXT,
  welcome_message  TEXT,
  guided_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
  branding         JSONB NOT NULL DEFAULT '{}'::jsonb,
  bot_user_id      VARCHAR(100) NOT NULL REFERENCES users(id),
  enabled          BOOLEAN NOT NULL DEFAULT true,
  rate_limit       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS kefu_instance_id VARCHAR(100) REFERENCES kefu_instances(id) ON DELETE SET NULL;
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS visitor_id     VARCHAR(100);
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS needs_human    BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS human_takeover BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_chat_sess_kefu ON ai_chat_sessions(kefu_instance_id, visitor_id);

INSERT INTO roles (id, name, description, is_system, is_superuser, default_page_access)
VALUES ('kefu-guest', '智能客服访客', '智能客服 bot 专用只读角色，可见数据页需显式授予', TRUE, FALSE, 'none')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS kefu_faq_items (
  id           VARCHAR(100) PRIMARY KEY,
  instance_id  VARCHAR(100) NOT NULL REFERENCES kefu_instances(id) ON DELETE CASCADE,
  question     TEXT NOT NULL,
  answer       TEXT NOT NULL,
  category     VARCHAR(100),
  sort_order   INTEGER NOT NULL DEFAULT 0,
  click_count  INTEGER NOT NULL DEFAULT 0,
  enabled      BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kefu_faq_instance ON kefu_faq_items(instance_id, sort_order);
"""


def migrate_kefu(conn) -> None:
    cur = conn.cursor()
    cur.execute(_SQL)
    conn.commit()


if __name__ == '__main__':
    with get_db() as c:
        migrate_kefu(c)
    print('kefu migration done')
