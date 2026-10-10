"""工具级耗时采集（SkillOpt 性能分析二期，spec §7）：

  agent_tool_calls.started_at / duration_ms   工具调用绝对开始与时长
                                              （来源 OC part state.time）
  idx_agent_tool_call_root                    性能聚合按 root+时窗取数
  idx_execution_manifest_kind_name            定义→任务关联按 (kind,name) 命中

全部幂等，可重复执行；旧数据不回填（duration_ms/started_at 为 NULL 合法）。

用法：python migrations/2026_10_10_tool_call_duration.py
或由 init_db 的 _run_dated_migrations / app.py 启动块调用 run()。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

DDL = [
    "ALTER TABLE agent_tool_calls "
    "ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
    "ALTER TABLE agent_tool_calls "
    "ADD COLUMN IF NOT EXISTS duration_ms INTEGER",
    "CREATE INDEX IF NOT EXISTS idx_agent_tool_call_root "
    "ON agent_tool_calls(root_session_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_execution_manifest_kind_name "
    "ON ai_execution_manifests(kind, name)",
]


def run():
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in DDL:
            cur.execute(stmt)
        conn.commit()
    print("tool call duration columns ready "
          "(started_at/duration_ms + root/manifest indexes).")


if __name__ == "__main__":
    run()
