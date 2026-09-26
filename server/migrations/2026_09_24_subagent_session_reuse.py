# -*- coding: utf-8 -*-
"""子代理会话复用（subagent session reuse）迁移。全部幂等：

1. ai_chat_batches.subagent_reuse JSONB —— 批级配置：哪些 subagent 需要复用
   会话（agent 名数组，如 ["dev","reviewer"]；空/NULL = 不启用）；
2. ai_subagent_pins —— 复用锚定登记：(root_session_id, agent) → task_id
   （OpenCode 子会话 id）。OC 插件 tool.execute.before 据此强制注入
   task_id、tool.execute.after 回写新会话 id；
3. ai_chat_subtasks.turn_segments JSONB —— 复用子会话的任务段
   [{ord, turn, label, firstMsgId, startedAt}]，每段以子会话里一次委派
   prompt（user 消息）为边界，供气泡展开渲染任务边界。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

BATCH_REUSE_DDL = """
ALTER TABLE ai_chat_batches
  ADD COLUMN IF NOT EXISTS subagent_reuse JSONB;
"""

PINS_DDL = """
CREATE TABLE IF NOT EXISTS ai_subagent_pins (
  id              VARCHAR(100) PRIMARY KEY,
  root_session_id VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  batch_id        VARCHAR(100) REFERENCES ai_chat_batches(id) ON DELETE CASCADE,
  agent           VARCHAR(200) NOT NULL,
  task_id         VARCHAR(100) NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_subagent_pin
  ON ai_subagent_pins(root_session_id, agent);
CREATE INDEX IF NOT EXISTS idx_subagent_pins_batch
  ON ai_subagent_pins(batch_id);
"""

SEGMENTS_DDL = """
ALTER TABLE ai_chat_subtasks
  ADD COLUMN IF NOT EXISTS turn_segments JSONB NOT NULL DEFAULT '[]'::jsonb;
"""


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(BATCH_REUSE_DDL)
        cur.execute(PINS_DDL)
        cur.execute(SEGMENTS_DDL)
        conn.commit()
    print("subagent session reuse schema ready.")


if __name__ == "__main__":
    run()
