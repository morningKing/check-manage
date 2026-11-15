# -*- coding: utf-8 -*-
"""AI 会话撤销/重做（2026-11-10 undo-redo spec §2.1）。

stash 搬出行而非行内标记：ai_chat_messages 有 22 个读取点（全局搜索/大纲/
perf/导出/open API…），搬出/搬回保真（seq/created_at/rating 随行携带，
search_text 触发器随 INSERT 重建），全部读取方零改动。幂等可重跑。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_message_stash (
                message_id  VARCHAR(100) PRIMARY KEY,
                session_id  VARCHAR(100) NOT NULL
                    REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
                role        VARCHAR(20) NOT NULL,
                content     JSONB NOT NULL,
                meta        JSONB,
                created_at  TIMESTAMPTZ NOT NULL,
                seq         BIGINT NOT NULL,
                rating      SMALLINT,
                stashed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_stash_session "
            "ON ai_chat_message_stash(session_id, seq)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_session_reverts (
                session_id  VARCHAR(100) PRIMARY KEY
                    REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
                mode        VARCHAR(10) NOT NULL,
                opencode_session_id VARCHAR(200) NOT NULL,
                oc_message_id VARCHAR(200),
                from_seq    BIGINT NOT NULL,
                message_count INT NOT NULL,
                file_count  INT NOT NULL DEFAULT 0,
                file_names  JSONB,
                prev_opencode_session_id VARCHAR(200),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
        cur.execute(
            "ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS "
            "context_replay_pending BOOLEAN NOT NULL DEFAULT FALSE")
