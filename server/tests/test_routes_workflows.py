"""集成测试：工作流 REST（定义 CRUD + 收件箱）。使用真实 DB（casemanage）。

conftest 的共享 `app`/`client` fixture mock 掉了 db.get_db。这些路由测试
需要 routes.workflows 命中真实 DB，故 force-rebind 其 `get_db` 到真实实现，
与 test_bypass_reseed.py 同一模式。
"""
import os
import sys
import importlib

import pytest
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db as _db_module  # noqa: E402
from db import get_db  # noqa: E402


@pytest.fixture
def client():
    """A Flask test client wired to the REAL dev DB (overrides conftest mock)."""
    from app import app as flask_app
    for mod_name in ('routes.workflows', 'auth'):
        mod = importlib.import_module(mod_name)
        if hasattr(mod, 'get_db'):
            mod.get_db = _db_module.get_db
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


def _admin_headers():
    from auth import create_token
    return {'Authorization': 'Bearer ' + create_token({'id': 'admin', 'username': 'admin', 'role': 'admin'})}


def test_definition_crud_via_api(client):
    h = _admin_headers()
    body = {'id': 'wf-api-1', 'name': '流A', 'enabled': True,
            'stages': [{'id': 's1', 'name': '评审', 'collection': 'req'}]}
    try:
        r = client.post('/workflow/definitions', json=body, headers=h)
        assert r.status_code == 200, r.get_data(as_text=True)
        r = client.get('/workflow/definitions', headers=h)
        assert any(x['id'] == 'wf-api-1' for x in r.get_json())
        r = client.delete('/workflow/definitions/wf-api-1', headers=h)
        assert r.status_code == 200
    finally:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM workflow_definitions WHERE id='wf-api-1'"); conn.commit()


def test_inbox_filters_by_role(client):
    h = _admin_headers()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM workflow_instances WHERE id='wfi-inbox'")
        cur.execute("DELETE FROM workflow_definitions WHERE id='wf-inbox'")
        cur.execute("INSERT INTO workflow_definitions (id,name,stages) VALUES ('wf-inbox','x',%s)",
                    (psycopg2.extras.Json([{'id': 's1', 'name': '评审', 'collection': 'req', 'assignedRoles': ['admin']}]),))
        cur.execute("INSERT INTO workflow_instances (id,workflow_id,status,current_stage_id,chain) "
                    "VALUES ('wfi-inbox','wf-inbox','running','s1',%s)",
                    (psycopg2.extras.Json([{'stageId': 's1', 'collection': 'req', 'recordId': 'r9'}]),))
        conn.commit()
    try:
        r = client.get('/workflow/inbox', headers=h)
        assert any(x['instanceId'] == 'wfi-inbox' for x in r.get_json())
    finally:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM workflow_instances WHERE id='wfi-inbox'")
            cur.execute("DELETE FROM workflow_definitions WHERE id='wf-inbox'"); conn.commit()
