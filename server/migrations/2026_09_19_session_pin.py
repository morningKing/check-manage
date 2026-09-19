# -*- coding: utf-8 -*-
"""幂等迁移：会话置顶。

ai_chat_sessions 加 pinned_at 列（NULL=未置顶；置顶记录时间，同位列内排序）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE ai_chat_sessions "
                    "ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ")
        conn.commit()
    print("session pin column ready.")

if __name__ == "__main__":
    run()
