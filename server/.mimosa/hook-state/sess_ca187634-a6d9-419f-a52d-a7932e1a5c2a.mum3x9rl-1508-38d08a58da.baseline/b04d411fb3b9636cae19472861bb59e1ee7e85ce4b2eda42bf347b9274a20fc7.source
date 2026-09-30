"""幂等迁移：建 dynamic_sequences 表 + 按现有数据播种计数器。
用法（server/ 下）：python -m migrations.2026_06_13_dynamic_sequences"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db
from utils.sequences import reseed_sequences

# DDL 与 init_db.py 中的 dynamic_sequences 保持同步
DDL = """
CREATE TABLE IF NOT EXISTS dynamic_sequences (
    collection    VARCHAR(200) NOT NULL,
    branch_id     VARCHAR(100) NOT NULL DEFAULT 'main',
    field_name    VARCHAR(200) NOT NULL,
    current_value BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (collection, branch_id, field_name)
);
"""


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(DDL)
        reseed_sequences(cur)
        conn.commit()
    return {"status": "ok"}


if __name__ == "__main__":
    print(run())
