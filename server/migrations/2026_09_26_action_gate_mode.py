# -*- coding: utf-8 -*-
"""幂等迁移:P3-C3 PreToolUse 门禁拦截——action_expectations 增加 mode 列。

- 'post' = 终态核对(既有行为,默认值——存量行全部回填 'post')
- 'pre'  = PreToolUse 拦截(deny list:不允许调用 args_pattern 匹配的工具,
           由 OpenCode 插件在工具调用前经 /ai/gate/internal/pre-check 校验)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE action_expectations "
            "ADD COLUMN IF NOT EXISTS mode VARCHAR(10) NOT NULL DEFAULT 'post'")
        # pre 行按 (scope_id, mode) 探测是工具调用前的热路径,给个窄索引
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_action_expectation_mode "
            "ON action_expectations(scope_id, mode)")
        conn.commit()
    print("action gate mode column ready.")

if __name__ == "__main__":
    run()
