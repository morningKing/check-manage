# -*- coding: utf-8 -*-
"""幂等迁移：会话自定义分组支持图标。

ai_chat_session_groups 加 icon 列（Element Plus 图标组件名，如 Folder、Collection）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE ai_chat_session_groups ADD COLUMN IF NOT EXISTS icon VARCHAR(60) NOT NULL DEFAULT 'Folder'")
        conn.commit()
    print("session groups icon column ready.")

if __name__ == "__main__":
    run()
