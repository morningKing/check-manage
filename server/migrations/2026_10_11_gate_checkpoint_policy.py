# -*- coding: utf-8 -*-
"""幂等迁移：动作门禁过程检查（checkpoint）与门禁策略库。

- action_expectations 加 checkpoint 承载列：prompt（检查提示词）/timing
  （before|after）/max_checks（注入预算）/fired_count（已注入计数）/
  assert_spec（终态兜底断言，账本型 tool 断言）。mode 列 VARCHAR(10)
  无 CHECK 约束，'checkpoint' 恰 10 字符直接可用。
- gate_policies：命名的门禁规则集（deny/post/checkpoint 混装），批引用即生效。
- ai_chat_batches.gate_policy_id：批引用策略（应用层校验，不加 FK——终态批
  的历史引用允许随策略删除，期望行快照已生效）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS prompt TEXT")
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS timing VARCHAR(8) NOT NULL DEFAULT 'before'")
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS max_checks INT NOT NULL DEFAULT 1")
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS fired_count INT NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS assert_spec JSONB")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS gate_policies ("
            " id TEXT PRIMARY KEY,"
            " name TEXT NOT NULL UNIQUE,"
            " description TEXT,"
            " checks JSONB NOT NULL DEFAULT '[]'::jsonb,"
            " enabled BOOLEAN NOT NULL DEFAULT TRUE,"
            " created_by TEXT,"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        cur.execute("ALTER TABLE ai_chat_batches "
                    "ADD COLUMN IF NOT EXISTS gate_policy_id TEXT")
        conn.commit()
    print("gate checkpoint/policy migration ready.")

if __name__ == "__main__":
    run()
