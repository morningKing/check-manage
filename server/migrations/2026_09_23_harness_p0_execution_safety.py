# -*- coding: utf-8 -*-
"""P0 执行安全基线迁移（ai-harness-p0 spec §5/§10.1）。

四件事，全部幂等：
1. ai_chat_turns 回合表 + 三个索引 —— 「同一子会话最多一个 active turn」由
   部分唯一索引 uniq_ai_chat_turns_active 在数据库层保证，不依赖应用判断；
2. ai_chat_sessions 增列 execution_generation / active_turn_id / gate_status /
   gate_error / gate_checked_at；
3. ai_batch_worker_leases 执行器租约表 —— 多进程部署时只有一个 batch worker；
4. UNIQUE(batch_id, batch_seq) —— 先回填去重（历史并发 append 可能已产生重复：
   保留每组最早一行，其余 batch_seq +1000 偏移）再建唯一索引。

执行审计表（2026_09_17）与本迁移无依赖，但 app.py 的启动钩子顺序上它在前。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

AI_CHAT_TURNS_DDL = """
CREATE TABLE IF NOT EXISTS ai_chat_turns (
  id                VARCHAR(100) PRIMARY KEY,
  session_id        VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  batch_id          VARCHAR(100) REFERENCES ai_chat_batches(id) ON DELETE CASCADE,
  user_id           VARCHAR(100) NOT NULL,
  client_request_id VARCHAR(100) NOT NULL,
  operation         VARCHAR(30)  NOT NULL,
  -- send | continue | retry | resume | reexecute | command
  status            VARCHAR(30)  NOT NULL DEFAULT 'accepted',
  -- accepted | running | completed | failed | stopped | cancelled | paused | recovering
  retry_of          VARCHAR(100) REFERENCES ai_chat_turns(id) ON DELETE SET NULL,
  attempt_id        VARCHAR(100),
  expected_generation BIGINT,
  error_code        VARCHAR(100),
  error_message     TEXT,
  started_at        TIMESTAMPTZ,
  last_event_at     TIMESTAMPTZ,
  finished_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_ai_chat_turns_client_req
  ON ai_chat_turns(session_id, client_request_id);
-- 同一子会话最多一个 active turn（ownership 的 DB 级保证）
CREATE UNIQUE INDEX IF NOT EXISTS uniq_ai_chat_turns_active
  ON ai_chat_turns(session_id)
  WHERE status IN ('accepted','running','recovering');
CREATE INDEX IF NOT EXISTS idx_ai_chat_turns_session_created
  ON ai_chat_turns(session_id, created_at DESC);
"""

SESSION_COLUMNS_DDL = """
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS execution_generation BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS active_turn_id VARCHAR(100),
  ADD COLUMN IF NOT EXISTS gate_status    VARCHAR(30),
  ADD COLUMN IF NOT EXISTS gate_error     TEXT,
  ADD COLUMN IF NOT EXISTS gate_checked_at TIMESTAMPTZ;
"""

WORKER_LEASES_DDL = """
CREATE TABLE IF NOT EXISTS ai_batch_worker_leases (
  lease_key      VARCHAR(50) PRIMARY KEY,        -- 'batch' | 'scan'
  owner_id       VARCHAR(200) NOT NULL,          -- host:pid:instance_uuid
  fencing_token  BIGINT NOT NULL DEFAULT 1,
  acquired_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  heartbeat_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  lease_until    TIMESTAMPTZ NOT NULL
);
"""


def _dedup_batch_seq(cur):
    """回填 batch_seq 重复行：每组 (batch_id, batch_seq) 保留 created_at,id
    最早的一行，其余 +1000 偏移腾位。偏移量远超单批文件上限（<=100），不会
    与既有 seq 相撞；重复再跑本函数时 WHERE 条件已为空集，天然幂等。"""
    cur.execute(
        """
        WITH dup AS (
          SELECT id, batch_id, batch_seq,
                 row_number() OVER (PARTITION BY batch_id, batch_seq
                                    ORDER BY created_at, id) AS rn
            FROM ai_chat_sessions
           WHERE batch_id IS NOT NULL AND batch_seq IS NOT NULL
        )
        UPDATE ai_chat_sessions s
           SET batch_seq = d.batch_seq + 1000
          FROM dup d
         WHERE s.id = d.id AND d.rn > 1
        """
    )
    return cur.rowcount


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(AI_CHAT_TURNS_DDL)
        cur.execute(SESSION_COLUMNS_DDL)
        cur.execute(WORKER_LEASES_DDL)
        moved = _dedup_batch_seq(cur)
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uniq_ai_chat_sessions_batch_seq "
            "ON ai_chat_sessions(batch_id, batch_seq) "
            "WHERE batch_id IS NOT NULL AND batch_seq IS NOT NULL"
        )
        conn.commit()
    if moved:
        print(f"harness p0: deduped {moved} duplicate batch_seq row(s) (+1000 offset).")
    print("harness p0 execution safety schema ready.")


if __name__ == "__main__":
    run()
