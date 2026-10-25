# -*- coding: utf-8 -*-
"""幂等迁移：批任务接管令牌表 + 子会话「接管续跑完成后重停」标记列。

continue_repark：paused 子会话经接管通道续跑时置 true；worker 收口时见到
true 则回到 paused 而非终态（不占 done/failed 计数），把控制权还给外部系统。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS batch_takeover_tokens ("
            " id TEXT PRIMARY KEY,"
            " token_hash TEXT NOT NULL UNIQUE,"
            " api_key_id TEXT NOT NULL,"
            " owner_user_id TEXT NOT NULL,"
            " batch_id TEXT NOT NULL,"
            " session_id TEXT NOT NULL,"
            " expires_at TIMESTAMPTZ NOT NULL,"
            " revoked_at TIMESTAMPTZ,"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " last_used_at TIMESTAMPTZ)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_takeover_tokens_session "
                    "ON batch_takeover_tokens (session_id)")
        cur.execute("ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS "
                    "continue_repark BOOLEAN NOT NULL DEFAULT false")
        conn.commit()
    print("batch takeover tokens migration ready.")


if __name__ == "__main__":
    run()
