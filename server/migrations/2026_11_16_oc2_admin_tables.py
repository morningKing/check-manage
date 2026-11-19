# -*- coding: utf-8 -*-
"""OC2 双栈后台管理（2026-11-16）：
1) ai_chat_sessions.failed_models JSONB——多模型故障切换的已试模型清单
   （批任务多模型配置下，某模型不可用时轮换下一模型；全败落终态）。
2) ai_oc_endpoints 表——自定义模型端点（后台管理）：写入各会话工作区
   opencode.json 的 provider 段（V1 格式，官方承诺 V2 内存规范化兼容——
   两栈通吃）。幂等可重跑。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE ai_chat_sessions "
                    "ADD COLUMN IF NOT EXISTS failed_models JSONB")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_oc_endpoints (
                id SERIAL PRIMARY KEY,
                provider_id VARCHAR(100) NOT NULL UNIQUE,
                display_name VARCHAR(200),
                base_url TEXT NOT NULL,
                api_key TEXT,
                models JSONB NOT NULL DEFAULT '[]'::jsonb,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_oc_endpoints_enabled "
                    "ON ai_oc_endpoints(enabled)")
        conn.commit()
