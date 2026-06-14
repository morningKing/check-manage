"""集成测试：工作流引擎在真实 update_item 路由下的端到端行为 + _workflowComment 泄漏防护。

覆盖此前未测的路径：
  - update_item 状态转换钩子 → on_transition → spawn 下游记录（端到端走真实 PUT 路由）
  - _workflowComment 不被写入记录 data（Bug-1 泄漏防护）
  - POST /workflow/instances 启动实例
  - 末阶段推进 → 实例 completed（引擎单测）
  - 错误角色推进被拒绝（引擎单测）

conftest 的共享 `app`/`client` fixture mock 掉了 db.get_db；这些测试需要命中真实
DB（casemanage）。与 test_routes_workflows.py 同一模式：force-rebind 相关模块的
`get_db` 到真实实现。因为本文件走的是 update_item 完整路径（分支/锁/通知/权限等
工具都各自 `from db import get_db`），故把所有持有 `get_db` 的已加载模块统一 rebind
回真实实现，避免 mock 残留导致连接错乱。
"""
import os
import sys
import importlib

import pytest
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db as _db_module  # noqa: E402
from db import get_db  # noqa: E402
from utils import workflow_repo as repo  # noqa: E402
from utils.workflow_engine import on_transition  # noqa: E402


@pytest.fixture
def client():
    """A Flask test client wired to the REAL dev DB (overrides conftest mock).

    Rebind every already-imported module that does `from db import get_db` back
    to the real implementation, so the full update_item path (route + branch +
    lock + notifier + permissions + workflow engine/repo) hits the live DB.
    """
    from app import app as flask_app
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
            mod_name.startswith('routes.') or mod_name.startswith('utils.')
            or mod_name == 'auth'
        ):
            try:
                mod.get_db = _db_module.get_db
            except (AttributeError, TypeError):
                pass
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


def _admin_headers():
    from auth import create_token
    return {'Authorization': 'Bearer ' + create_token(
        {'id': 'admin', 'username': 'admin', 'role': 'admin'})}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _wf_status_field(roles=('admin',)):
    """A status field carrying a 待评审→已通过 workflowConfig transition."""
    return {
        'fieldName': 'status', 'label': '状态', 'controlType': 'select',
        'workflowConfig': {
            'enabled': True,
            'transitions': [
                {'from': '待评审', 'to': '已通过', 'roles': list(roles)},
            ],
        },
    }


def _seed_page(cur, coll, fields):
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                (f'page-{coll}', coll, psycopg2.extras.Json(fields)))


def _cleanup(colls=(), defs=(), insts=()):
    with get_db() as conn:
        cur = conn.cursor()
        for coll in colls:
            cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
            cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
            cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        for iid in insts:
            cur.execute("DELETE FROM workflow_instances WHERE id=%s", (iid,))
        for wid in defs:
            cur.execute("DELETE FROM workflow_definitions WHERE id=%s", (wid,))
        conn.commit()


# --------------------------------------------------------------------------- #
# 1. leak: _workflowComment must NOT be persisted into record data
# --------------------------------------------------------------------------- #
def test_workflow_comment_not_stored_in_record(client):
    coll, rid = 'zzwfc', 'zzwfc-r1'
    h = _admin_headers()
    with get_db() as conn:
        cur = conn.cursor()
        _seed_page(cur, coll, [_wf_status_field(roles=['admin']),
                               {'fieldName': 'title', 'controlType': 'text'}])
        cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) "
                    "VALUES (%s,%s,%s,'main')",
                    (rid, coll, psycopg2.extras.Json({'status': '待评审', 'title': 'A'})))
        conn.commit()
    try:
        r = client.put(f'/{coll}/{rid}',
                       json={'status': '已通过', 'title': 'A', '_version': 1,
                             '_workflowComment': '看起来不错'},
                       headers=h)
        assert r.status_code == 200, r.get_data(as_text=True)
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT data FROM dynamic_data WHERE collection=%s AND id=%s",
                        (coll, rid))
            record_data = cur.fetchone()[0]
        assert record_data['status'] == '已通过'
        assert '_workflowComment' not in record_data, \
            f'_workflowComment leaked into stored data: {record_data}'
    finally:
        _cleanup(colls=[coll])


# --------------------------------------------------------------------------- #
# 2. update_item hook end-to-end: PUT advances the running instance + spawns downstream
# --------------------------------------------------------------------------- #
def test_update_hook_advances_workflow_instance(client):
    req, down = 'zzwfreq', 'zzwfdown'
    wid, iid, rid = 'wf-zzwf-hook', 'wfi-zzwf-hook', 'zzwfreq-r1'
    h = _admin_headers()
    with get_db() as conn:
        cur = conn.cursor()
        _seed_page(cur, req, [_wf_status_field(roles=['admin']),
                              {'fieldName': 'title', 'controlType': 'text'}])
        _seed_page(cur, down, [
            {'fieldName': 'dcode', 'controlType': 'autoSequence',
             'sequenceConfig': {'prefix': 'D-', 'max': 999}, 'isPrimaryKey': True},
            {'fieldName': 'title', 'controlType': 'text'},
            {'fieldName': 'srcReq', 'controlType': 'text'},
        ])
        repo.save_definition(cur, {
            'id': wid, 'name': 'hook-demo', 'enabled': True, 'stages': [
                {'id': 's1', 'name': '评审', 'collection': req, 'statusField': 'status',
                 'advanceTransition': {'from': '待评审', 'to': '已通过'},
                 'assignedRoles': ['admin'],
                 'spawn': {'fieldMapping': {'title': '$source.title', 'srcReq': '$source.id'}}},
                {'id': 's2', 'name': '设计', 'collection': down, 'statusField': 'dstatus',
                 'advanceTransition': {'from': '设计中', 'to': '完成'},
                 'assignedRoles': ['admin']},
            ]})
        cur.execute("DELETE FROM workflow_instances WHERE id=%s", (iid,))
        cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) "
                    "VALUES (%s,%s,%s,'main')",
                    (rid, req, psycopg2.extras.Json({'status': '待评审', 'title': '登录'})))
        repo.create_instance(cur, iid, wid, 's1', req, rid, 'admin')
        conn.commit()
    try:
        r = client.put(f'/{req}/{rid}',
                       json={'status': '已通过', 'title': '登录', '_version': 1,
                             '_workflowComment': 'ok'},
                       headers=h)
        assert r.status_code == 200, r.get_data(as_text=True)
        with get_db() as conn:
            cur = conn.cursor()
            inst = repo.get_instance(cur, iid)
            assert inst['current_stage_id'] == 's2', inst
            assert inst['status'] == 'running'
            assert len(inst['chain']) == 2
            down_id = inst['chain'][1]['recordId']
            cur.execute("SELECT data FROM dynamic_data WHERE id=%s", (down_id,))
            d = cur.fetchone()[0]
        assert d['title'] == '登录'
        assert d['srcReq'] == rid
        assert d['dcode'] == 'D-001'
    finally:
        _cleanup(colls=[req, down], defs=[wid], insts=[iid])


# --------------------------------------------------------------------------- #
# 3. POST /workflow/instances starts an instance at the first stage
# --------------------------------------------------------------------------- #
def test_start_instance_api(client):
    coll = 'zzwfstart'
    wid, rid = 'wf-zzwf-start', 'zzwfstart-r1'
    h = _admin_headers()
    with get_db() as conn:
        cur = conn.cursor()
        _seed_page(cur, coll, [_wf_status_field(roles=['admin'])])
        repo.save_definition(cur, {
            'id': wid, 'name': 'start-demo', 'enabled': True, 'stages': [
                {'id': 's1', 'name': '评审', 'collection': coll, 'statusField': 'status',
                 'advanceTransition': {'from': '待评审', 'to': '已通过'},
                 'assignedRoles': ['admin']},
            ]})
        cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) "
                    "VALUES (%s,%s,%s,'main')",
                    (rid, coll, psycopg2.extras.Json({'status': '待评审'})))
        conn.commit()
    created_iid = None
    try:
        r = client.post('/workflow/instances',
                        json={'workflowId': wid, 'collection': coll, 'recordId': rid},
                        headers=h)
        assert r.status_code == 201, r.get_data(as_text=True)
        inst = r.get_json()
        created_iid = inst['id']
        assert inst['current_stage_id'] == 's1'
        assert inst['status'] == 'running'
        assert inst['chain'][0]['recordId'] == rid
        assert inst['chain'][0]['collection'] == coll
    finally:
        _cleanup(colls=[coll], defs=[wid], insts=[created_iid] if created_iid else [])


# --------------------------------------------------------------------------- #
# 4. advancing the LAST stage completes the instance (engine unit)
# --------------------------------------------------------------------------- #
def test_advance_last_stage_completes():
    coll = 'zzwfend'
    wid, iid, rid = 'wf-zzwf-end', 'wfi-zzwf-end', 'zzwfend-r1'
    with get_db() as conn:
        cur = conn.cursor()
        _seed_page(cur, coll, [_wf_status_field(roles=['admin'])])
        repo.save_definition(cur, {
            'id': wid, 'name': 'end-demo', 'enabled': True, 'stages': [
                {'id': 's1', 'name': '评审', 'collection': coll, 'statusField': 'status',
                 'advanceTransition': {'from': '待评审', 'to': '已通过'},
                 'assignedRoles': ['admin']},
            ]})
        cur.execute("DELETE FROM workflow_instances WHERE id=%s", (iid,))
        cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) "
                    "VALUES (%s,%s,%s,'main')",
                    (rid, coll, psycopg2.extras.Json({'status': '待评审'})))
        repo.create_instance(cur, iid, wid, 's1', coll, rid, 'admin')
        conn.commit()
    try:
        with get_db() as conn:
            cur = conn.cursor()
            on_transition(cur, collection=coll, record_id=rid, status_field='status',
                          from_value='待评审', to_value='已通过',
                          old_data={'status': '待评审'}, new_data={'status': '已通过'},
                          operator='admin', role='admin')
            conn.commit()
            inst = repo.get_instance(cur, iid)
        assert inst['status'] == 'completed', inst
        assert inst['current_stage_id'] == 's1'
        assert len(inst['chain']) == 1  # no downstream spawned
    finally:
        _cleanup(colls=[coll], defs=[wid], insts=[iid])


# --------------------------------------------------------------------------- #
# 5. wrong-role advance is denied: instance stays put, no downstream spawned (engine unit)
# --------------------------------------------------------------------------- #
def test_advance_denied_for_wrong_role():
    req, down = 'zzwfdreq', 'zzwfddown'
    wid, iid, rid = 'wf-zzwf-deny', 'wfi-zzwf-deny', 'zzwfdreq-r1'
    with get_db() as conn:
        cur = conn.cursor()
        _seed_page(cur, req, [_wf_status_field(roles=['reviewer'])])
        _seed_page(cur, down, [
            {'fieldName': 'dcode', 'controlType': 'autoSequence',
             'sequenceConfig': {'prefix': 'D-', 'max': 999}, 'isPrimaryKey': True}])
        repo.save_definition(cur, {
            'id': wid, 'name': 'deny-demo', 'enabled': True, 'stages': [
                {'id': 's1', 'name': '评审', 'collection': req, 'statusField': 'status',
                 'advanceTransition': {'from': '待评审', 'to': '已通过'},
                 'assignedRoles': ['reviewer'],
                 'spawn': {'fieldMapping': {}}},
                {'id': 's2', 'name': '设计', 'collection': down, 'statusField': 'dstatus',
                 'advanceTransition': {'from': '设计中', 'to': '完成'}},
            ]})
        cur.execute("DELETE FROM workflow_instances WHERE id=%s", (iid,))
        cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) "
                    "VALUES (%s,%s,%s,'main')",
                    (rid, req, psycopg2.extras.Json({'status': '待评审'})))
        repo.create_instance(cur, iid, wid, 's1', req, rid, 'admin')
        conn.commit()
    try:
        with get_db() as conn:
            cur = conn.cursor()
            on_transition(cur, collection=req, record_id=rid, status_field='status',
                          from_value='待评审', to_value='已通过',
                          old_data={'status': '待评审'}, new_data={'status': '已通过'},
                          operator='guestuser', role='guest')
            conn.commit()
            inst = repo.get_instance(cur, iid)
            cur.execute("SELECT COUNT(*) FROM dynamic_data WHERE collection=%s", (down,))
            down_count = cur.fetchone()[0]
        assert inst['current_stage_id'] == 's1', inst  # did NOT advance
        assert inst['status'] == 'running'
        assert len(inst['chain']) == 1
        assert down_count == 0  # nothing spawned
    finally:
        _cleanup(colls=[req, down], defs=[wid], insts=[iid])
