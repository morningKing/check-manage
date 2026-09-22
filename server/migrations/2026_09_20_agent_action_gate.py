# -*- coding: utf-8 -*-
"""幂等迁移:Agent 动作账本与到位门禁。

设计:docs/design/AI子任务动作账本与到位门禁设计.md
- agent_tool_calls      工具调用账本(幂等键 oc_session_id+part_id,条件更新防写放大)
- action_expectations   到位期望(派发前登记,终态核对)
- ai_chat_batches.action_checks / ai_chat_prompt_templates.action_checks
                        批定义与模板上的期望承载字段(入口 A/B)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_tool_calls (
                id              BIGSERIAL PRIMARY KEY,
                oc_session_id   VARCHAR(100) NOT NULL,
                root_session_id VARCHAR(100),
                subtask_id      VARCHAR(100)
                                REFERENCES ai_chat_subtasks(id) ON DELETE CASCADE,
                message_id      VARCHAR(100),
                part_id         VARCHAR(100) NOT NULL,
                tool            VARCHAR(50)  NOT NULL,
                args_text       TEXT,
                state           VARCHAR(20),
                occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_tool_call_part
                ON agent_tool_calls(oc_session_id, part_id)""")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_tool_call_q
                ON agent_tool_calls(oc_session_id, tool, state)""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS action_expectations (
                id              BIGSERIAL PRIMARY KEY,
                scope_type      VARCHAR(20)  NOT NULL,
                scope_id        VARCHAR(100) NOT NULL,
                name            VARCHAR(100) NOT NULL,
                tool            VARCHAR(50)  NOT NULL,
                args_pattern    TEXT         NOT NULL,
                require_state   VARCHAR(20)  NOT NULL DEFAULT 'completed',
                min_count       INT          NOT NULL DEFAULT 1,
                source          VARCHAR(30)  NOT NULL DEFAULT 'batch',
                last_status     VARCHAR(20),
                last_checked_at TIMESTAMPTZ,
                last_evidence   INT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (scope_type, scope_id, name)
            )""")
        cur.execute("ALTER TABLE ai_chat_batches "
                    "ADD COLUMN IF NOT EXISTS action_checks JSONB")
        cur.execute("ALTER TABLE ai_chat_prompt_templates "
                    "ADD COLUMN IF NOT EXISTS action_checks JSONB")
        # M3 效果断言原语:check_type='tool'(默认,账本匹配)|'file'(工作区文件存在)
        # |'db_record'(dynamic_data 记录存在);effect_spec 承载 file/db 的参数。
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS check_type VARCHAR(20) "
                    "NOT NULL DEFAULT 'tool'")
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS effect_spec JSONB")
        # 定向能力:子代理级(subagents 过滤,按 ai_chat_subtasks.agent 核对)
        # 与账本的 agent 名记录(子代理动作归属)
        cur.execute("ALTER TABLE agent_tool_calls "
                    "ADD COLUMN IF NOT EXISTS agent VARCHAR(100)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_tool_call_agent "
                    "ON agent_tool_calls(agent)")
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS subagents JSONB")
        conn.commit()
    print("agent action gate tables ready.")

if __name__ == "__main__":
    run()
