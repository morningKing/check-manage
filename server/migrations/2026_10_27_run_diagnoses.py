# -*- coding: utf-8 -*-
"""幂等迁移：O1 统一运行诊断（ai_run_diagnoses）。

一次 attempt 一张结构化多维诊断行：dimensions JSONB 固定含五个维度键
（task_result/rule_compliance/promise_action/skill_fit/performance），每维
{status: pass|fail|uncertain, summary, evidence[]}。overall ∈
success|partial|fail|uncertain。UNIQUE(attempt_id, generator_version)
支撑诊断器升级后按版本重跑并存（对比用），同版本 upsert 幂等。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_run_diagnoses (
                id                TEXT PRIMARY KEY,
                attempt_id        TEXT NOT NULL
                                  REFERENCES ai_execution_attempts(id),
                root_session_id   TEXT,
                overall           VARCHAR(20) NOT NULL,
                dimensions        JSONB NOT NULL,
                generator_version VARCHAR(20) NOT NULL,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (attempt_id, generator_version)
            )""")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_run_diag_root
                ON ai_run_diagnoses(root_session_id, created_at DESC)""")
        conn.commit()
    print("run diagnoses migration ready.")

if __name__ == "__main__":
    run()
