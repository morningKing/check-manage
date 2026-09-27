import json
import psycopg2.extras
from db import get_db
from utils.sequences import reseed_sequences, seq_max_from_data


def _setup_page(cur, collection, seq_field, prefix):
    page_id = f'page-{collection}'
    fields = [{'fieldName': seq_field, 'controlType': 'autoSequence',
               'sequenceConfig': {'prefix': prefix, 'max': 999}, 'isPrimaryKey': True}]
    cur.execute("DELETE FROM page_configs WHERE id=%s", (page_id,))
    cur.execute("INSERT INTO page_configs (id, name, fields) VALUES (%s,%s,%s)",
                (page_id, collection, psycopg2.extras.Json(fields)))


def _add_record(cur, collection, rid, data, branch='main'):
    cur.execute("INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,%s)",
                (rid, collection, psycopg2.extras.Json(data), branch))


def test_seq_max_and_reseed(db_conn):
    cur = db_conn.cursor()
    coll = 'zzseqtest'
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
    _setup_page(cur, coll, 'code', 'IC-')
    _add_record(cur, coll, 'r1', {'code': 'IC-003'})
    _add_record(cur, coll, 'r2', {'code': 'IC-007'})
    _add_record(cur, coll, 'r3', {'code': 'IC-002'})
    db_conn.commit()

    assert seq_max_from_data(cur, coll, 'main', 'code', 'IC-') == 7

    reseed_sequences(cur)
    db_conn.commit()
    cur.execute("SELECT current_value FROM dynamic_sequences WHERE collection=%s AND branch_id='main' AND field_name='code'", (coll,))
    assert cur.fetchone()[0] == 7

    cur.execute("UPDATE dynamic_sequences SET current_value=20 WHERE collection=%s AND field_name='code'", (coll,))
    db_conn.commit()
    reseed_sequences(cur)
    db_conn.commit()
    cur.execute("SELECT current_value FROM dynamic_sequences WHERE collection=%s AND field_name='code'", (coll,))
    assert cur.fetchone()[0] == 20  # GREATEST 不回退到 7

    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    db_conn.commit()


def test_seq_max_skips_non_numeric(db_conn):
    cur = db_conn.cursor()
    coll = 'zzseqbad'
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    _add_record(cur, coll, 'b1', {'code': 'IC-005'})
    _add_record(cur, coll, 'b2', {'code': 'IC-abc'})   # 非数字后缀，跳过
    _add_record(cur, coll, 'b3', {'code': 'IC-'})       # 空后缀，跳过
    _add_record(cur, coll, 'b4', {'code': '009'})       # 缺前缀，跳过（prefix='IC-'）
    _add_record(cur, coll, 'b5', {'other': 'x'})        # 无该字段，跳过
    db_conn.commit()
    assert seq_max_from_data(cur, coll, 'main', 'code', 'IC-') == 5
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    db_conn.commit()


def test_reseed_multi_branch(db_conn):
    cur = db_conn.cursor()
    coll = 'zzseqbranch'
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
    _setup_page(cur, coll, 'code', 'IC-')
    _add_record(cur, coll, 'm1', {'code': 'IC-004'}, branch='main')
    _add_record(cur, coll, 'f1', {'code': 'IC-011'}, branch='feat-x')
    db_conn.commit()
    reseed_sequences(cur)   # branch_id=None → 两个分支各自播种
    db_conn.commit()
    cur.execute("SELECT branch_id, current_value FROM dynamic_sequences WHERE collection=%s ORDER BY branch_id", (coll,))
    rows = dict(cur.fetchall())
    assert rows['main'] == 4
    assert rows['feat-x'] == 11   # 分支计数器独立
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    db_conn.commit()
