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
