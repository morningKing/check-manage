# -*- coding: utf-8 -*-
"""会话级权限模式列（2026-10-31 bypass spec §2.1）。

'ask'=照常弹权限卡（默认）；'bypass'=监听器自动放行权限询问（once）。
新会话一律 'ask'（安全默认，不继承）。幂等可重跑。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE ai_chat_sessions "
                    "ADD COLUMN IF NOT EXISTS permission_mode VARCHAR(10) DEFAULT 'ask'")
