# -*- coding: utf-8 -*-
"""P2 编排与 Runtime 迁移（ai-harness-p2 spec §5）。全部幂等：

- ai_orchestration_definitions（(id,version) 主键，发布后不可变）；
- ai_orchestration_runs / ai_orchestration_steps（Run/Step 两层，attempt
  复用 P1 的 ai_execution_attempts）；
- ai_approval_requests（执行前审批；uniq_approval_pending_step 保证每 step
  至多一个 pending）；
- artifacts / artifact_refs（内容寻址产物存储登记）；
- ai_runtime_manifests（run 冻结的运行时清单）；
- ai_chat_sessions 增 orchestration_run_id / orchestration_step_id——step 的
  执行体就是批 worker 里的子会话（复用 P0/P1 全部安全语义）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

DEFINITIONS_DDL = """
CREATE TABLE IF NOT EXISTS ai_orchestration_definitions (
  id          VARCHAR(100) NOT NULL,
  version     INT NOT NULL DEFAULT 1,
  name        VARCHAR(200) NOT NULL,
  description TEXT,
  enabled     BOOLEAN NOT NULL DEFAULT TRUE,
  owner_user_id VARCHAR(100),
  nodes       JSONB NOT NULL DEFAULT '[]'::jsonb,
  edges       JSONB NOT NULL DEFAULT '[]'::jsonb,
  retry_policy      JSONB NOT NULL DEFAULT '{}'::jsonb,
  timeout_policy    JSONB NOT NULL DEFAULT '{}'::jsonb,
  budget_policy     JSONB NOT NULL DEFAULT '{}'::jsonb,
  approval_policy   JSONB NOT NULL DEFAULT '{}'::jsonb,
  compensation_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_at TIMESTAMPTZ,
  PRIMARY KEY (id, version)
);
"""

RUNS_DDL = """
CREATE TABLE IF NOT EXISTS ai_orchestration_runs (
  id            VARCHAR(100) PRIMARY KEY,
  definition_id VARCHAR(100) NOT NULL,
  definition_version INT NOT NULL,
  status        VARCHAR(30) NOT NULL DEFAULT 'pending',
  -- pending | running | waiting_approval | paused | recovering
  -- | partial | completed | failed | cancelled | needs_review
  current_nodes JSONB NOT NULL DEFAULT '[]'::jsonb,
  run_input_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  runtime_manifest_id VARCHAR(100),
  budget_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  requested_by  VARCHAR(100),
  requested_by_kind VARCHAR(20) NOT NULL DEFAULT 'user',
  error_code    VARCHAR(100),
  error_message TEXT,
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_orch_runs_status ON ai_orchestration_runs(status, created_at DESC);
"""

STEPS_DDL = """
CREATE TABLE IF NOT EXISTS ai_orchestration_steps (
  id            VARCHAR(150) PRIMARY KEY,        -- run_id + ':' + node_id
  run_id        VARCHAR(100) NOT NULL REFERENCES ai_orchestration_runs(id) ON DELETE CASCADE,
  node_id       VARCHAR(100) NOT NULL,
  kind          VARCHAR(30) NOT NULL,            -- agent | approval | join
  name          VARCHAR(300),
  status        VARCHAR(30) NOT NULL DEFAULT 'blocked',
  -- blocked | runnable | running | waiting_approval | succeeded | failed
  -- | skipped | needs_review
  depends_on    JSONB NOT NULL DEFAULT '[]'::jsonb,
  node_def      JSONB NOT NULL DEFAULT '{}'::jsonb,
  session_id    VARCHAR(100),
  attempt_count INT NOT NULL DEFAULT 0,
  output        JSONB,
  error_code    VARCHAR(100),
  error_message TEXT,
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_orch_steps_run ON ai_orchestration_steps(run_id, node_id);
CREATE INDEX IF NOT EXISTS idx_orch_steps_status ON ai_orchestration_steps(status);
"""

APPROVALS_DDL = """
CREATE TABLE IF NOT EXISTS ai_approval_requests (
  id            VARCHAR(100) PRIMARY KEY,
  run_id        VARCHAR(100) NOT NULL REFERENCES ai_orchestration_runs(id) ON DELETE CASCADE,
  step_id       VARCHAR(150),
  risk_level    VARCHAR(20) NOT NULL DEFAULT 'medium',
  tool          VARCHAR(100),
  args_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
  effect_summary TEXT,
  status        VARCHAR(30) NOT NULL DEFAULT 'pending',
  -- pending | approved | rejected | expired | cancelled
  requested_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
  requested_users JSONB NOT NULL DEFAULT '[]'::jsonb,
  decided_by    VARCHAR(100),
  decision_comment TEXT,
  decision_hash VARCHAR(64),
  requested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at    TIMESTAMPTZ,
  resolved_at   TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_approval_pending_step
  ON ai_approval_requests(step_id) WHERE status = 'pending';
"""

ARTIFACTS_DDL = """
CREATE TABLE IF NOT EXISTS artifacts (
  id          VARCHAR(100) PRIMARY KEY,
  owner_user_id VARCHAR(100),
  name        VARCHAR(300) NOT NULL,
  media_type  VARCHAR(150),
  size_bytes  BIGINT NOT NULL DEFAULT 0,
  sha256      VARCHAR(64) NOT NULL,
  storage_key TEXT NOT NULL,
  status      VARCHAR(30) NOT NULL DEFAULT 'active',
  retention_days INT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at  TIMESTAMPTZ
);
-- 唯一索引由 2026_09_25_artifact_owner_scope.py 统一创建（owner 隔离口径）。
-- 此处不得再建全局 uniq_artifact_sha：boot 重放会在 M9 DROP 之后复活全局
-- 去重索引，与 owner 隔离冲突（12 号 §4.3）。

CREATE TABLE IF NOT EXISTS artifact_refs (
  id          VARCHAR(100) PRIMARY KEY,
  artifact_id VARCHAR(100) NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  run_id      VARCHAR(100),
  step_id     VARCHAR(150),
  attempt_id  VARCHAR(100),
  session_id  VARCHAR(100),
  batch_id    VARCHAR(100),
  relation    VARCHAR(30) NOT NULL,   -- input | output | checkpoint | report
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_artifact_refs_run ON artifact_refs(run_id, step_id);
CREATE INDEX IF NOT EXISTS idx_artifact_refs_artifact ON artifact_refs(artifact_id);
"""

MANIFESTS_DDL = """
CREATE TABLE IF NOT EXISTS ai_runtime_manifests (
  id            VARCHAR(100) PRIMARY KEY,
  runtime_kind  VARCHAR(30) NOT NULL,        -- opencode_local | docker | windows_job | k8s_job
  runtime_version VARCHAR(100),
  model         VARCHAR(300),
  agent_hashes  JSONB NOT NULL DEFAULT '{}'::jsonb,
  skill_hashes  JSONB NOT NULL DEFAULT '{}'::jsonb,
  plugin_hashes JSONB NOT NULL DEFAULT '{}'::jsonb,
  mcp_config_hash VARCHAR(64),
  guidance_hash VARCHAR(64),
  workspace_config_hash VARCHAR(64),
  resource_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
  network_policy   JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

SESSION_LINK_DDL = """
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS orchestration_run_id  VARCHAR(100)
    REFERENCES ai_orchestration_runs(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS orchestration_step_id VARCHAR(150);
CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_orch
  ON ai_chat_sessions(orchestration_run_id, orchestration_step_id);
"""


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(DEFINITIONS_DDL)
        cur.execute(RUNS_DDL)
        cur.execute(STEPS_DDL)
        cur.execute(APPROVALS_DDL)
        cur.execute(ARTIFACTS_DDL)
        cur.execute(MANIFESTS_DDL)
        cur.execute(SESSION_LINK_DDL)
        conn.commit()
    print("harness p2 orchestration schema ready.")


if __name__ == "__main__":
    run()
