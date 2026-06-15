"""DAG + 条件边路由回归：显式 edges、单条件路由、默认边、显式回退边。直连真实 DB。"""
import psycopg2.extras
from db import get_db
from utils import workflow_repo as repo
from utils.workflow_engine import on_transition, _eval_condition


def _page(cur, coll, fields):
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                (f'page-{coll}', coll, psycopg2.extras.Json(fields)))


SRC, HI, LO = 'zzdagsrc', 'zzdaghigh', 'zzdaglow'


def _setup(cur, priority):
    for coll in (SRC, HI, LO):
        _page(cur, coll, [{'fieldName': 'code', 'controlType': 'autoSequence',
                           'sequenceConfig': {'prefix': 'D-', 'max': 999}, 'isPrimaryKey': True},
                          {'fieldName': 'title', 'controlType': 'text'}])
    wf = {'id': 'wf-dag', 'name': 'dag', 'enabled': True,
          'stages': [
              {'id': 's1', 'name': '初审', 'collection': SRC, 'statusField': 'status',
               'advanceTransition': {'from': '待审', 'to': '已审'}, 'assignedRoles': ['admin'],
               'spawn': {'fieldMapping': {'title': '$source.title'}}},
              {'id': 's2', 'name': '高优', 'collection': HI, 'statusField': 'st2',
               'advanceTransition': {'from': 'a', 'to': 'b'}},
              {'id': 's3', 'name': '常规', 'collection': LO, 'statusField': 'st3',
               'advanceTransition': {'from': 'a', 'to': 'b'}},
          ],
          'edges': [
              {'id': 'e1', 'source': 's1', 'target': 's2', 'kind': 'advance',
               'condition': {'field': 'priority', 'op': '==', 'value': 'high'}},
              {'id': 'e2', 'source': 's1', 'target': 's3', 'kind': 'advance'},  # 默认边
          ]}
    repo.save_definition(cur, wf)
    cur.execute("DELETE FROM workflow_instances WHERE id='inst-dag'")
    cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES ('rec-dag',%s,%s,'main')",
                (SRC, psycopg2.extras.Json({'status': '待审', 'title': '登录', 'priority': priority})))
    repo.create_instance(cur, 'inst-dag', 'wf-dag', 's1', SRC, 'rec-dag', 'admin')


def _advance(cur, priority):
    on_transition(cur, SRC, 'rec-dag', 'status', '待审', '已审',
                  {'status': '待审', 'priority': priority},
                  {'status': '已审', 'title': '登录', 'priority': priority}, 'admin', 'admin')


def test_condition_routes_to_high():
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur, 'high'); conn.commit()
        _advance(cur, 'high'); conn.commit()
        inst = repo.get_instance(cur, 'inst-dag')
        cur.execute("SELECT COUNT(*) FROM dynamic_data WHERE collection=%s", (HI,)); hi = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM dynamic_data WHERE collection=%s", (LO,)); lo = cur.fetchone()[0]
    assert inst['current_stage_id'] == 's2'
    assert hi == 1 and lo == 0


def test_default_edge_when_condition_unmet():
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur, 'low'); conn.commit()
        _advance(cur, 'low'); conn.commit()
        inst = repo.get_instance(cur, 'inst-dag')
        cur.execute("SELECT COUNT(*) FROM dynamic_data WHERE collection=%s", (HI,)); hi = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM dynamic_data WHERE collection=%s", (LO,)); lo = cur.fetchone()[0]
    assert inst['current_stage_id'] == 's3'
    assert hi == 0 and lo == 1


def test_explicit_reject_edge_routes_to_target():
    """显式 reject 边把回退导向指定阶段（非相邻）。"""
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur, 'high')
        d = repo.get_definition(cur, 'wf-dag')
        # s2 配回退转换 + 一条 reject 边 s2→s1
        d['stages'][1]['rejectTransition'] = {'from': '退', 'to': '回'}
        d['edges'].append({'id': 'e3', 'source': 's2', 'target': 's1', 'kind': 'reject'})
        repo.save_definition(cur, d)
        conn.commit()
        _advance(cur, 'high'); conn.commit()  # s1 → s2
        inst = repo.get_instance(cur, 'inst-dag'); down = inst['chain'][1]['recordId']
        on_transition(cur, HI, down, 'st2', '退', '回', {'st2': '退'}, {'st2': '回'}, 'admin', 'admin', comment='补')
        conn.commit()
        inst2 = repo.get_instance(cur, 'inst-dag')
    assert inst2['current_stage_id'] == 's1'


def test_eval_condition_ops():
    assert _eval_condition({'field': 'p', 'op': '==', 'value': 'x'}, {'p': 'x'}) is True
    assert _eval_condition({'field': 'p', 'op': '!=', 'value': 'x'}, {'p': 'y'}) is True
    assert _eval_condition({'field': 'n', 'op': '>', 'value': '100'}, {'n': 200}) is True
    assert _eval_condition({'field': 'n', 'op': '>', 'value': '100'}, {'n': 50}) is False
    assert _eval_condition({'field': 'n', 'op': '<=', 'value': '10'}, {'n': '10'}) is True
    assert _eval_condition({'field': 't', 'op': 'contains', 'value': 'ab'}, {'t': 'xabz'}) is True
    assert _eval_condition({'field': 'n', 'op': '>', 'value': 'x'}, {'n': 'y'}) is False  # 非数值 → 不命中
