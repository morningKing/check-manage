# -*- coding: utf-8 -*-
"""会话绑定 OC 栈列（OC2 适配阶段一，2026-11-11）。

oc_runtime：'v1'=OpenCode 1.x（默认，存量全部 'v1'）；'v2'=OpenCode 2.x。
会话一旦创建即钉死所在栈，全生命周期（继续/恢复/压缩/子会话/撤销）不跨栈。
路由层 fail-closed：行缺失/NULL/未知值一律按 'v1'（见 ai.runtime.registry）。
幂等可重跑。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE ai_chat_sessions "
                    "ADD COLUMN IF NOT EXISTS oc_runtime VARCHAR(10) DEFAULT 'v1'")
