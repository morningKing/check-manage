# -*- coding: utf-8 -*-
"""P1 持久化执行迁移（ai-harness-p1 spec §5/§11.1）。全部幂等：

1. ai_chat_sessions 增执行租约列（lease_owner/lease_until/heartbeat_at/
   fencing_token）——租约的持有者是子会话行（claim 单元）；
2. ai_execution_attempts 增租约/恢复/预算列；建"同会话至多一个 running
   attempt"部分唯一索引——建索引前先把会话已不在 running 状态的悬挂
   attempt 收口为 'orphaned'（P1-4 的存量修复）；
3. 七张新表：checkpoints / effects / commands / batch_events / delivery_outbox /
   budgets / usage；
4. ai_batch_worker_leases 增 lease_kind（dispatcher|delivery|scheduler），
   outbox 投递器与定时任务各自独立租约。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

SESSION_LEASE_DDL = """
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS lease_owner    VARCHAR(200),
  ADD COLUMN IF NOT EXISTS lease_until    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS heartbeat_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fencing_token  BIGINT NOT NULL DEFAULT 0;
"""

ATTEMPT_LEASE_DDL = """
ALTER TABLE ai_execution_attempts
  ADD COLUMN IF NOT EXISTS lease_owner        VARCHAR(200),
  ADD COLUMN IF NOT EXISTS lease_until        TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS heartbeat_at       TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fencing_token      BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS queue_started_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS dispatch_started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_progress_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS recovery_reason    VARCHAR(50),
  ADD COLUMN IF NOT EXISTS checkpoint_id      VARCHAR(100),
  ADD COLUMN IF NOT EXISTS budget_snapshot    JSONB;
"""

# P1-4 存量收口：会话已不在 running 的悬挂 running attempt → orphaned
ORPHAN_ATTEMPTS_SQL = """
UPDATE ai_execution_attempts a
   SET status = 'orphaned', finished_at = COALESCE(a.finished_at, NOW()),
       error_code = COALESCE(a.error_code, 'ORPHANED'),
       recovery_reason = 'migration_orphan_sweep'
  FROM ai_chat_sessions s
 WHERE a.session_id = s.id
   AND a.status IN ('accepted', 'running', 'recovering')
   AND s.status <> 'running'
"""

# 注：spec §5.1 原文把 'recovering' 也纳入谓词，但实现里 recovering 是旧
# attempt 被重排前的收口态（审计保留，见 batch_engine._close_attempt_for_requeue），
# 新 attempt 在其之后立即创建——若把 recovering 算 active 会挡住合法的续链。
# 因此谓词只含 claimed/running，"同会话至多一个真正在跑的 attempt"不变量保持。
ATTEMPT_RUNNING_UQ = """
CREATE UNIQUE INDEX IF NOT EXISTS uniq_attempt_running_per_session
  ON ai_execution_attempts(session_id)
  WHERE status IN ('claimed', 'running')
"""

CHECKPOINTS_DDL = """
CREATE TABLE IF NOT EXISTS ai_execution_checkpoints (
  id                     VARCHAR(100) PRIMARY KEY,
  session_id             VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  attempt_id             VARCHAR(100) REFERENCES ai_execution_attempts(id) ON DELETE SET NULL,
  execution_generation   BIGINT NOT NULL DEFAULT 0,
  checkpoint_type        VARCHAR(30) NOT NULL,
  -- dispatch | progress | turn_complete | recovery | manual
  message_seq            INTEGER,
  opencode_session_id    VARCHAR(100),
  workspace_manifest_hash VARCHAR(64),
  completed_effect_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
  artifact_refs          JSONB NOT NULL DEFAULT '[]'::jsonb,
  context_snapshot       JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_latest              BOOLEAN NOT NULL DEFAULT TRUE,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_checkpoint_latest
  ON ai_execution_checkpoints(session_id) WHERE is_latest;
CREATE INDEX IF NOT EXISTS idx_checkpoint_session_created
  ON ai_execution_checkpoints(session_id, created_at DESC);
"""

EFFECTS_DDL = """
CREATE TABLE IF NOT EXISTS ai_execution_effects (
  id              VARCHAR(100) PRIMARY KEY,
  session_id      VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  attempt_id      VARCHAR(100),
  batch_id        VARCHAR(100),
  step_key        VARCHAR(100),
  effect_type     VARCHAR(50) NOT NULL,
  -- mcp_write | file_import | scan_writeback | callback | artifact
  idempotency_key VARCHAR(200) NOT NULL,
  request_hash    VARCHAR(64),
  status          VARCHAR(30) NOT NULL DEFAULT 'planned',
  -- planned | started | committed | failed | unknown | compensated
  external_ref    TEXT,
  result_hash     VARCHAR(64),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  committed_at    TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_effect_scope_key
  ON ai_execution_effects(session_id, effect_type, idempotency_key);
"""

COMMANDS_DDL = """
CREATE TABLE IF NOT EXISTS ai_execution_commands (
  id                  VARCHAR(100) PRIMARY KEY,
  idempotency_key     VARCHAR(200) NOT NULL,
  batch_id            VARCHAR(100),
  session_id          VARCHAR(100),
  command_type        VARCHAR(30) NOT NULL,
  -- pause | resume | cancel | retry | continue | reexecute | force_stop
  requested_by        VARCHAR(100),
  requested_by_kind   VARCHAR(20) NOT NULL DEFAULT 'user',
  payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
  expected_generation BIGINT,
  status              VARCHAR(30) NOT NULL DEFAULT 'accepted',
  -- accepted | applied | rejected | expired | failed
  result_snapshot     JSONB,
  error_code          VARCHAR(100),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  applied_at          TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_command_idem
  ON ai_execution_commands(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_command_batch_created
  ON ai_execution_commands(batch_id, created_at DESC);
"""

BATCH_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS ai_batch_events (
  batch_id     VARCHAR(100) NOT NULL,
  event_seq    BIGINT NOT NULL,
  event_id     VARCHAR(100) NOT NULL,
  event_type   VARCHAR(50) NOT NULL,
  -- batch.created | batch.status | child.status | gate.evaluated
  -- child.recovered | budget.exceeded | command.applied
  -- delivery.sent | delivery.failed
  aggregate_type VARCHAR(30) NOT NULL,
  aggregate_id   VARCHAR(100),
  execution_generation BIGINT,
  payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (batch_id, event_seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_batch_event_id ON ai_batch_events(event_id);
CREATE INDEX IF NOT EXISTS idx_batch_events_created
  ON ai_batch_events(batch_id, created_at DESC);
"""

OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS ai_delivery_outbox (
  id              VARCHAR(100) PRIMARY KEY,
  event_id        VARCHAR(100) NOT NULL,
  batch_id        VARCHAR(100) NOT NULL,
  event_type      VARCHAR(50) NOT NULL,
  target_url      TEXT NOT NULL,
  payload         JSONB NOT NULL,
  signature       TEXT,
  idempotency_key VARCHAR(200) NOT NULL,
  status          VARCHAR(30) NOT NULL DEFAULT 'pending',
  -- pending | sending | delivered | failed | dead_letter
  attempt_count   INT NOT NULL DEFAULT 0,
  next_retry_at   TIMESTAMPTZ,
  last_error      TEXT,
  delivered_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_outbox_event
  ON ai_delivery_outbox(event_id, target_url);
CREATE INDEX IF NOT EXISTS idx_outbox_due
  ON ai_delivery_outbox(status, next_retry_at)
  WHERE status IN ('pending', 'failed');
"""

BUDGETS_DDL = """
CREATE TABLE IF NOT EXISTS ai_execution_budgets (
  id            VARCHAR(100) PRIMARY KEY,
  scope_type    VARCHAR(20) NOT NULL,   -- batch | user | api_key
  scope_id      VARCHAR(100) NOT NULL,
  max_wall_clock_ms BIGINT,
  max_tokens        BIGINT,
  max_cost          NUMERIC(12,4),
  max_tool_calls    INT,
  max_subagents     INT,
  max_workspace_bytes BIGINT,
  max_concurrency   INT,
  on_exceed     VARCHAR(20) NOT NULL DEFAULT 'drain',  -- warn | drain | abort
  enabled       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_budget_scope
  ON ai_execution_budgets(scope_type, scope_id);

CREATE TABLE IF NOT EXISTS ai_execution_usage (
  session_id     VARCHAR(100) PRIMARY KEY REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  batch_id       VARCHAR(100),
  tokens_input   BIGINT NOT NULL DEFAULT 0,
  tokens_output  BIGINT NOT NULL DEFAULT 0,
  cost           NUMERIC(12,4) NOT NULL DEFAULT 0,
  wall_clock_ms  BIGINT NOT NULL DEFAULT 0,
  tool_calls     INT NOT NULL DEFAULT 0,
  subagents      INT NOT NULL DEFAULT 0,
  workspace_bytes BIGINT NOT NULL DEFAULT 0,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usage_batch ON ai_execution_usage(batch_id);
"""

LEASE_KIND_DDL = """
ALTER TABLE ai_batch_worker_leases
  ADD COLUMN IF NOT EXISTS lease_kind VARCHAR(30) NOT NULL DEFAULT 'dispatcher';
"""


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(SESSION_LEASE_DDL)
        cur.execute(ATTEMPT_LEASE_DDL)
        cur.execute(ORPHAN_ATTEMPTS_SQL)
        cur.execute(ATTEMPT_RUNNING_UQ)
        cur.execute(CHECKPOINTS_DDL)
        cur.execute(EFFECTS_DDL)
        cur.execute(COMMANDS_DDL)
        cur.execute(BATCH_EVENTS_DDL)
        cur.execute(OUTBOX_DDL)
        cur.execute(BUDGETS_DDL)
        cur.execute(LEASE_KIND_DDL)
        conn.commit()
    print("harness p1 durable execution schema ready.")


if __name__ == "__main__":
    run()
