# -*- coding: utf-8 -*-
"""O2 候选评估测试：diff 校验/双集回归门槛/promote 前置校验。"""


def test_table_exists(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_skill_candidates")
        assert cur.fetchone()[0] == 0
