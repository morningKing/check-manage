"""幂等迁移：AI 执行合规审计与 SkillOpt 数据模型（execution-audit Spec §8）。

新增七张表，覆盖 Spec 的执行事实层与诊断层：

  ai_execution_attempts          统一执行 Attempt（交互/批/扫描/客服/OpenAPI/分析/子代理）
  ai_execution_prompt_snapshots  Effective Prompt 快照（hash/增强项/受控正文）
  ai_execution_manifests         本次执行可见的 Agent/Skill/Guidance manifest（含 SHA256）
  ai_execution_events            不可变执行事件（append-only，event_seq 幂等）
  ai_execution_contracts         Skill/Agent 执行契约（explicit | inferred）
  ai_execution_step_results      契约步骤审计结果（三方比对：契约/Todo 声明/实际证据）
  ai_execution_diagnoses         结构化诊断报告（分析任务 = 诊断行）

全部 CREATE TABLE IF NOT EXISTS / 幂等索引，可重复执行。旧会话不回填伪造数据，
报告层用 data_completeness 表达缺口（Spec §17.1）。

用法（在 server/ 目录下）：
    python -m migrations.2026_09_17_execution_audit_tables
也可由 init_db.py 在建库流程末尾调用 run()，保证全新库直接带表。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

DDL = [
    # ── 1. 统一执行 Attempt ─────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS ai_execution_attempts (
      id                    VARCHAR(100) PRIMARY KEY,
      session_id            VARCHAR(100) NOT NULL
                             REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
      source_type           VARCHAR(30) NOT NULL,
      -- interactive | batch | scan | kefu | open_api | row_action |
      -- trace_analysis | subagent
      source_id             VARCHAR(100),
      parent_attempt_id     VARCHAR(100)
                             REFERENCES ai_execution_attempts(id) ON DELETE SET NULL,
      attempt_no            INTEGER NOT NULL DEFAULT 1,
      operation             VARCHAR(30) NOT NULL DEFAULT 'send',
      -- send | retry | continue | resume | reexecute | command | analysis
      requested_agent       VARCHAR(200),
      effective_agent       VARCHAR(200),
      requested_model       VARCHAR(300),
      effective_model       VARCHAR(300),
      agent_resolution      VARCHAR(30) NOT NULL DEFAULT 'unknown',
      -- requested | session_default | batch_default | runtime_default |
      -- fallback | unknown
      model_resolution      VARCHAR(30) NOT NULL DEFAULT 'unknown',
      raw_prompt_hash       VARCHAR(64),
      effective_prompt_hash VARCHAR(64),
      effective_prompt_len  INTEGER,
      prompt_version        VARCHAR(50),
      workspace_path        TEXT,
      workspace_config_hash VARCHAR(64),
      guidance_hash         VARCHAR(64),
      status                VARCHAR(30) NOT NULL DEFAULT 'accepted',
      -- accepted | running | completed | failed | stopped | orphaned | recovering
      error_code            VARCHAR(100),
      error_message         TEXT,
      started_at            TIMESTAMPTZ,
      finished_at           TIMESTAMPTZ,
      created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ai_execution_attempt_session_no "
    "ON ai_execution_attempts(session_id, attempt_no)",
    "CREATE INDEX IF NOT EXISTS ai_execution_attempt_source_idx "
    "ON ai_execution_attempts(source_type, source_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ai_execution_attempt_status_idx "
    "ON ai_execution_attempts(status, created_at DESC)",
    # ── 2. Effective Prompt 快照（受控访问） ────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS ai_execution_prompt_snapshots (
      id                    VARCHAR(100) PRIMARY KEY,
      attempt_id            VARCHAR(100) NOT NULL
                             REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
      raw_user_content      TEXT,
      effective_prompt      TEXT,
      raw_user_content_hash VARCHAR(64),
      effective_prompt_hash VARCHAR(64) NOT NULL,
      effective_prompt_len  INTEGER NOT NULL DEFAULT 0,
      augmentations         JSONB NOT NULL DEFAULT '{}'::jsonb,
      context_snapshot      JSONB NOT NULL DEFAULT '{}'::jsonb,
      redaction_status      VARCHAR(30) NOT NULL DEFAULT 'redacted',
      -- redacted | plaintext
      created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ai_prompt_snapshot_attempt_idx "
    "ON ai_execution_prompt_snapshots(attempt_id)",
    # ── 3. Agent/Skill/Guidance manifest ───────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS ai_execution_manifests (
      id                VARCHAR(100) PRIMARY KEY,
      attempt_id        VARCHAR(100) NOT NULL
                        REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
      kind              VARCHAR(30) NOT NULL,
      -- skill | agent | guidance | mcp | workspace_config
      name              VARCHAR(300) NOT NULL,
      source            VARCHAR(40) NOT NULL,
      -- runtime_global | platform_global | project | session | generated
      path              TEXT,
      content_hash      VARCHAR(64),
      version_label     VARCHAR(200),
      injected          BOOLEAN NOT NULL DEFAULT FALSE,
      injection_status  VARCHAR(30) NOT NULL DEFAULT 'unknown',
      runtime_loaded    VARCHAR(30) NOT NULL DEFAULT 'unknown',
      selected          VARCHAR(30) NOT NULL DEFAULT 'unknown',
      invoked           VARCHAR(30) NOT NULL DEFAULT 'unknown',
      evidence_refs     JSONB NOT NULL DEFAULT '[]'::jsonb,
      created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ai_execution_manifest_name_idx "
    "ON ai_execution_manifests(name, content_hash, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ai_execution_manifest_attempt_idx "
    "ON ai_execution_manifests(attempt_id)",
    # ── 4. 不可变执行事件 ───────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS ai_execution_events (
      id                VARCHAR(150) PRIMARY KEY,
      attempt_id        VARCHAR(100) NOT NULL
                        REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
      event_seq         BIGINT NOT NULL,
      event_type        VARCHAR(80) NOT NULL,
      occurred_at       TIMESTAMPTZ,
      received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      session_id        VARCHAR(100),
      parent_session_id VARCHAR(100),
      message_id        VARCHAR(200),
      part_id           VARCHAR(200),
      tool_call_id      VARCHAR(200),
      parent_part_id    VARCHAR(200),
      subtask_id        VARCHAR(200),
      status            VARCHAR(40),
      payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
      redaction_status  VARCHAR(30) NOT NULL DEFAULT 'redacted',
      UNIQUE(attempt_id, event_seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ai_execution_event_attempt_seq_idx "
    "ON ai_execution_events(attempt_id, event_seq)",
    "CREATE INDEX IF NOT EXISTS ai_execution_event_tool_idx "
    "ON ai_execution_events(attempt_id, tool_call_id, event_seq)",
    "CREATE INDEX IF NOT EXISTS ai_execution_event_time_idx "
    "ON ai_execution_events(occurred_at)",
    # ── 5. 执行契约 ─────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS ai_execution_contracts (
      id                VARCHAR(100) PRIMARY KEY,
      name              VARCHAR(300) NOT NULL,
      owner_type        VARCHAR(30) NOT NULL,
      -- skill | agent | task_template
      owner_id          VARCHAR(300),
      source            VARCHAR(30) NOT NULL,
      -- explicit | inferred
      content_hash      VARCHAR(64),
      schema_version    VARCHAR(30) NOT NULL DEFAULT 'v1',
      contract          JSONB NOT NULL,
      confidence        NUMERIC(4,3),
      active            BOOLEAN NOT NULL DEFAULT TRUE,
      created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ai_execution_contract_owner_idx "
    "ON ai_execution_contracts(owner_type, owner_id, active)",
    # ── 6. 契约步骤审计结果 ─────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS ai_execution_step_results (
      id                 VARCHAR(150) PRIMARY KEY,
      attempt_id         VARCHAR(100) NOT NULL
                         REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
      contract_id        VARCHAR(100)
                         REFERENCES ai_execution_contracts(id) ON DELETE SET NULL,
      step_id            VARCHAR(200) NOT NULL,
      expected           BOOLEAN NOT NULL DEFAULT FALSE,
      declared_by_agent  BOOLEAN NOT NULL DEFAULT FALSE,
      observed           BOOLEAN NOT NULL DEFAULT FALSE,
      status             VARCHAR(40) NOT NULL,
      -- completed_confirmed | completed_claimed | missing | skipped |
      -- out_of_order | failed | unknown_due_to_missing_data
      evidence_level     VARCHAR(30) NOT NULL DEFAULT 'unknown',
      confidence         NUMERIC(4,3),
      started_at         TIMESTAMPTZ,
      finished_at        TIMESTAMPTZ,
      duration_ms        INTEGER,
      evidence_refs      JSONB NOT NULL DEFAULT '[]'::jsonb,
      reason             TEXT,
      created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE(attempt_id, step_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ai_execution_step_status_idx "
    "ON ai_execution_step_results(status, created_at DESC)",
    # ── 7. 结构化诊断（分析任务行） ─────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS ai_execution_diagnoses (
      id                  VARCHAR(100) PRIMARY KEY,
      attempt_id          VARCHAR(100)
                          REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
      target_session_id   VARCHAR(100)
                          REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
      analysis_session_id VARCHAR(100),
      status              VARCHAR(30) NOT NULL DEFAULT 'pending',
      -- pending | running | completed | partial | failed
      data_completeness   NUMERIC(4,3),
      report              JSONB NOT NULL DEFAULT '{}'::jsonb,
      report_schema       VARCHAR(30) NOT NULL DEFAULT 'v1',
      requested_model     VARCHAR(300),
      requested_by        VARCHAR(100),
      error_message       TEXT,
      created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      completed_at        TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS ai_execution_diagnosis_target_idx "
    "ON ai_execution_diagnoses(target_session_id, created_at DESC)",
]


def run():
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in DDL:
            cur.execute(stmt)
        conn.commit()
    print("execution audit tables ready "
          "(attempts/prompt_snapshots/manifests/events/contracts/step_results/diagnoses).")


if __name__ == "__main__":
    run()
