import psycopg2.extras
from db import get_db
from utils.workflow_engine import spawn_record


def _mkpage(cur, coll, with_seq=True):
    fields = []
    if with_seq:
        fields.append({'fieldName': 'code', 'controlType': 'autoSequence',
                       'sequenceConfig': {'prefix': 'DS-', 'max': 999}, 'isPrimaryKey': True})
    fields.append({'fieldName': 'title', 'controlType': 'text'})
    fields.append({'fieldName': 'fromReq', 'controlType': 'text'})
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                (f'page-{coll}', coll, psycopg2.extras.Json(fields)))


def test_spawn_allocates_sequence_and_maps_fields():
    coll = 'zzwfdown'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        _mkpage(cur, coll)
        conn.commit()
        src = {'id': 'req-1', 'title': '登录需求'}
        new_id = spawn_record(cur, target_collection=coll, branch_id='main',
                              field_mapping={'title': '$source.title', 'fromReq': '$source.id'},
                              source_data=src, source_id='req-1', operator='admin')
        conn.commit()
        cur.execute("SELECT data FROM dynamic_data WHERE id=%s", (new_id,))
        data = cur.fetchone()[0]
    assert data['title'] == '登录需求'
    assert data['fromReq'] == 'req-1'
    assert data['code'] == 'DS-001'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        conn.commit()
