import psycopg2.extras
from db import get_db
from utils.sequences import reseed_sequences, allocate_sequence


def test_reseed_after_data_change_prevents_collision():
    """模拟还原把编号更大的数据写回后，重播种使后续分配不撞号。"""
    coll = 'zzrestore'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        fields = [{'fieldName': 'code', 'controlType': 'autoSequence',
                   'sequenceConfig': {'prefix': 'IC-', 'max': 999}, 'isPrimaryKey': True}]
        cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                    (f'page-{coll}', coll, psycopg2.extras.Json(fields)))
        cur.execute("INSERT INTO dynamic_sequences VALUES (%s,'main','code',5)", (coll,))
        cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES (%s,%s,%s,'main')",
                    ('big', coll, psycopg2.extras.Json({'code': 'IC-030'})))
        conn.commit()
        reseed_sequences(cur, collections=[coll])
        conn.commit()
        nxt = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 3, count=1)[0]
        conn.commit()
    assert nxt == 'IC-031'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        conn.commit()
