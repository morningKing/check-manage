# -*- coding: utf-8 -*-
"""O1 运行诊断测试：维度判定规则 / overall 聚合 / 幂等 / fail 必带证据。"""


def test_table_exists(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_run_diagnoses")
        assert cur.fetchone()[0] == 0
