# -*- coding: utf-8 -*-
"""幂等迁移:批子任务软删除。

ai_chat_sessions 加 deleted_at(NULL=未删除)。管理员软删除后,前台批次
详情/侧栏不再显示该子任务,数据保留(审计与恢复的后续可能)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE ai_chat_sessions "
                    "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_deleted "
                    "ON ai_chat_sessions(deleted_at) WHERE deleted_at IS NOT NULL")
        conn.commit()
    print("batch child soft-delete column ready.")

if __name__ == "__main__":
    run()
