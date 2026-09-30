# -*- coding: utf-8 -*-
"""幂等迁移：SkillOpt P2 —— Skill 调用精确采集与建议效果追踪。

- ai_skill_invocations：Skill 调用记录（source=runtime 事件证明实际调用；
  heuristic 为启发推断），含 skill_hash 支撑版本对比
- ai_suggestion_feedback：建议接受/拒绝/应用与前后指标（效果追踪）
- ai_execution_events 保留策略按 payload 老化分层清理（30 天明文/180 天行）

幂等可重复执行。用法（server/ 目录）：python -m migrations.2026_09_18_skillopt_p2
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

DDL = [
    """
    CREATE TABLE IF NOT EXISTS ai_skill_invocations (
      id              VARCHAR(100) PRIMARY KEY,
      session_id      VARCHAR(100) REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
      attempt_id      VARCHAR(100) REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
      skill_name      VARCHAR(300) NOT NULL,
      skill_hash      VARCHAR(64),
      source          VARCHAR(30) NOT NULL DEFAULT 'heuristic',
      -- runtime | heuristic
      evidence_level  VARCHAR(30) NOT NULL DEFAULT 'inferred',
      -- confirmed | inferred
      invoked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      completed_at    TIMESTAMPTZ,
      outcome         VARCHAR(30),
      -- completed | failed | abandoned
      tool_calls      INTEGER,
      duration_ms     INTEGER,
      evidence_refs   JSONB NOT NULL DEFAULT '[]'::jsonb,
      UNIQUE (attempt_id, skill_name, skill_hash)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_skill_inv_name_time "
    "ON ai_skill_invocations(skill_name, skill_hash, invoked_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_skill_inv_attempt "
    "ON ai_skill_invocations(attempt_id)",
    """
    CREATE TABLE IF NOT EXISTS ai_suggestion_feedback (
      id              VARCHAR(100) PRIMARY KEY,
      diagnosis_id    VARCHAR(100)
                      REFERENCES ai_execution_diagnoses(id) ON DELETE CASCADE,
      suggestion_id   VARCHAR(200) NOT NULL,
      action          VARCHAR(30) NOT NULL,
      -- accepted | rejected | modified | applied | rolled_back
      applied_value   TEXT,
      applied_by      VARCHAR(100),
      applied_at      TIMESTAMPTZ,
      before_metrics  JSONB NOT NULL DEFAULT '{}'::jsonb,
      after_metrics   JSONB NOT NULL DEFAULT '{}'::jsonb,
      created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sugg_fb_diag "
    "ON ai_suggestion_feedback(diagnosis_id, created_at DESC)",
]


def run():
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in DDL:
            cur.execute(stmt)
        conn.commit()
    print("skillopt p2 tables ready (invocations + suggestion feedback).")


if __name__ == "__main__":
    run()
