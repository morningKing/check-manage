"""幂等迁移：AI 会话自定义分组（sidebar session grouping）。

- ai_chat_session_groups：用户自定义分组（本人维度）
- ai_chat_sessions.group_id：会话归属（NULL=未分组；删组置 NULL，不删会话）
- 轨迹分析会话（kind=trace_analysis）不走该表——它由系统分组渲染，
  不可移动、不可删除。

用法（在 server/ 目录下）：
    python -m migrations.2026_09_18_session_groups
由 init_db / app 启动钩子自动执行（幂等）。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

DDL = [
    """
    CREATE TABLE IF NOT EXISTS ai_chat_session_groups (
      id          VARCHAR(100) PRIMARY KEY,
      user_id     VARCHAR(100) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      name        VARCHAR(100) NOT NULL,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (user_id, name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ai_chat_session_groups_user "
    "ON ai_chat_session_groups(user_id, created_at)",
    "ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS group_id "
    "VARCHAR(100) REFERENCES ai_chat_session_groups(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_group "
    "ON ai_chat_sessions(group_id)",
]


def run():
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in DDL:
            cur.execute(stmt)
        conn.commit()
    print("session groups ready (ai_chat_session_groups + sessions.group_id).")


if __name__ == "__main__":
    run()
