"""幂等迁移：创建 workflow_definitions / workflow_instances 表。
用法（server/ 下）：python -m migrations.2026_06_14_workflow_tables"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

# DDL 与 init_db.py 中的 workflow_* 保持同步
DDL = """
CREATE TABLE IF NOT EXISTS workflow_definitions (
    id VARCHAR(100) PRIMARY KEY, name VARCHAR(200) NOT NULL, description TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE, stages JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS workflow_instances (
    id VARCHAR(100) PRIMARY KEY, workflow_id VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'running', current_stage_id VARCHAR(100),
    chain JSONB NOT NULL DEFAULT '[]'::jsonb, history JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMPTZ DEFAULT NOW(), started_by VARCHAR(100), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wf_inst_status ON workflow_instances(status);
CREATE INDEX IF NOT EXISTS idx_wf_inst_current ON workflow_instances(current_stage_id);
CREATE INDEX IF NOT EXISTS idx_wf_inst_workflow ON workflow_instances(workflow_id);
"""


def run():
    with get_db() as conn:
        conn.cursor().execute(DDL)
        conn.commit()
    return {"status": "ok"}


if __name__ == "__main__":
    print(run())
