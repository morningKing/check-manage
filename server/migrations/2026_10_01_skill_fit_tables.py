"""SkillOpt 任务拟合：拟合结果表 / 定义版本表 / 子代理归属列（2026-10-01）。

新增两张表 + 一列，覆盖 task-fit 的结果层与定义层：

  ai_skill_fit_results             attempt 收敛时轨迹 vs 定义步骤的拟合结果
                                   （spec §3.2）
  ai_skill_def_versions            定义版本实体（content_hash 可读化，
                                   spec §3.4）
  ai_skill_invocations.subtask_id  subagent skill 调用的归属标注（spec §5c）

全部 CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / 幂等索引，
可重复执行。

用法（在 server/ 目录下）：
    python migrations/2026_10_01_skill_fit_tables.py
或
    python -m migrations.2026_10_01_skill_fit_tables
也可由 init_db.py 在建库流程末尾经 _run_dated_migrations() 调用 run()，
保证全新库直接带表。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

DDL = [
    # ── 1. 拟合结果：轨迹 vs 定义步骤 ─────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS ai_skill_fit_results (
      id            VARCHAR(100) PRIMARY KEY,
      attempt_id    VARCHAR(100) NOT NULL
                     REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
      session_id    VARCHAR(100) NOT NULL,
      def_kind      VARCHAR(30) NOT NULL,
      def_name      VARCHAR(300) NOT NULL,
      def_hash      VARCHAR(64),
      steps_total   INTEGER NOT NULL DEFAULT 0,
      steps_hit     INTEGER NOT NULL DEFAULT 0,
      score         INTEGER NOT NULL DEFAULT 0,
      status        VARCHAR(20) NOT NULL DEFAULT 'unknown',
      per_step      JSONB NOT NULL DEFAULT '[]'::jsonb,
      diagnosis     JSONB,
      computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ai_skill_fit_attempt_idx "
    "ON ai_skill_fit_results(attempt_id)",
    "CREATE INDEX IF NOT EXISTS ai_skill_fit_def_idx "
    "ON ai_skill_fit_results(def_name, computed_at DESC)",
    # ── 2. 定义版本实体（UNIQUE 去重，重复拟合只记一次） ──────────────
    """
    CREATE TABLE IF NOT EXISTS ai_skill_def_versions (
      id             VARCHAR(100) PRIMARY KEY,
      def_kind       VARCHAR(30) NOT NULL,
      def_name       VARCHAR(300) NOT NULL,
      content_hash   VARCHAR(64) NOT NULL,
      version_label  VARCHAR(200),
      note           TEXT,
      first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (def_kind, def_name, content_hash)
    )
    """,
    # ── 3. 子代理 skill 调用归属列 ───────────────────────────────────
    "ALTER TABLE ai_skill_invocations "
    "ADD COLUMN IF NOT EXISTS subtask_id VARCHAR(100)",
    "CREATE INDEX IF NOT EXISTS idx_skill_inv_subtask "
    "ON ai_skill_invocations(subtask_id)",
]


def run():
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in DDL:
            cur.execute(stmt)
        conn.commit()
    print("skill fit tables ready "
          "(fit_results/def_versions/invocations.subtask_id).")


if __name__ == "__main__":
    run()
