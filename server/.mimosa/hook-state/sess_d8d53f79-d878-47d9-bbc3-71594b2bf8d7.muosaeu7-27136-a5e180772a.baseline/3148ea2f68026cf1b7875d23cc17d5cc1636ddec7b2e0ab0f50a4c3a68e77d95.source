import psycopg2.extras
from db import get_db
from utils import workflow_repo as repo
from utils.workflow_engine import on_transition


def _setup(cur):
    for coll, fields in [
        ('zzreq', [{'fieldName': 'status', 'controlType': 'select'},
                   {'fieldName': 'title', 'controlType': 'text'}]),
        ('zzdesign', [{'fieldName': 'dcode', 'controlType': 'autoSequence',
                       'sequenceConfig': {'prefix': 'D-', 'max': 999}, 'isPrimaryKey': True},
                      {'fieldName': 'title', 'controlType': 'text'},
                      {'fieldName': 'srcReq', 'controlType': 'text'}]),
    ]:
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                    (f'page-{coll}', coll, psycopg2.extras.Json(fields)))
    wf = {'id': 'wf-adv', 'name': 'demo', 'enabled': True, 'stages': [
        {'id': 's1', 'name': '评审', 'collection': 'zzreq', 'statusField': 'status',
         'advanceTransition': {'from': '待评审', 'to': '已通过'}, 'assignedRoles': ['admin'],
         'spawn': {'fieldMapping': {'title': '$source.title', 'srcReq': '$source.id'}, 'linkBackField': 'srcReq'}},
        {'id': 's2', 'name': '设计', 'collection': 'zzdesign', 'statusField': 'dstatus',
         'advanceTransition': {'from': '设计中', 'to': '完成'}, 'assignedRoles': ['admin']},
    ]}
    repo.save_definition(cur, wf)
    cur.execute("DELETE FROM workflow_instances WHERE id='inst-adv'")
    cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES ('req-a','zzreq',%s,'main')",
                (psycopg2.extras.Json({'status': '待评审', 'title': '登录'}),))
    repo.create_instance(cur, 'inst-adv', 'wf-adv', 's1', 'zzreq', 'req-a', 'admin')


def test_advance_spawns_downstream_and_moves_instance():
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur); conn.commit()
        on_transition(cur, collection='zzreq', record_id='req-a', status_field='status',
                      from_value='待评审', to_value='已通过',
                      old_data={'status': '待评审', 'title': '登录'},
                      new_data={'status': '已通过', 'title': '登录'}, operator='admin', role='admin')
        conn.commit()
        inst = repo.get_instance(cur, 'inst-adv')
        assert inst['current_stage_id'] == 's2'
        assert len(inst['chain']) == 2
        down_id = inst['chain'][1]['recordId']
        cur.execute("SELECT data FROM dynamic_data WHERE id=%s", (down_id,))
        d = cur.fetchone()[0]
        assert d['title'] == '登录' and d['srcReq'] == 'req-a'
        assert d['dcode'] == 'D-001'
