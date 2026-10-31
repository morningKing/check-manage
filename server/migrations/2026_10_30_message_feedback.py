# -*- coding: utf-8 -*-
"""消息点赞/点踩反馈表（2026-10-30 第一档差距补齐 Feature A）。

独立小表而非消息表加列：ai_chat_messages.meta 由服务端持久化占用，热点
表不加列；后续 SkillOpt 消费反馈时本表自带 session_id 可直接 JOIN。
幂等可重跑。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_message_feedback (
                message_id VARCHAR(100) PRIMARY KEY
                    REFERENCES ai_chat_messages(id) ON DELETE CASCADE,
                session_id VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
                rating     SMALLINT NOT NULL CHECK (rating IN (1, -1)),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_feedback_sess "
            "ON ai_chat_message_feedback(session_id)")
