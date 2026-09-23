"""/ai/data-internal/execute 内部转发端点测试(真实开发库)。

MCP 数据写工具的转发通道:以会话用户身份走真实应用层路由。这里验证
鉴权/白名单收紧与转发语义(autoSequence 分配、操作日志、乐观锁、菜单
唯一性)在真实库上成立。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def _internal_headers():
    # config 在 app 导入时才加载 server/.env,不能在模块 import 时读环境变量
    from config import MCP_INTERNAL_TOKEN
    return {'X-Internal-Token': MCP_INTERNAL_TOKEN, 'Content-Type': 'application/json'}


def _execute(client, payload, headers=None):
    return client.post('/ai/data-internal/execute', json=payload,
                       headers=headers or _internal_headers())


@pytest.fixture
def setup_app(db_conn):
    import json
    import db as db_module
    db_module.pool = None
    uid = f'u-dataint-{uuid.uuid4().hex[:8]}'
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s,%s,'x',%s,'admin')",
            (uid, 'dataint-' + uid, uid))
        suffix = uuid.uuid4().hex[:8]
        collection = f'mcp-probe-{suffix}'
        page_id = f'page-{collection}'
        fields = [
            {'id': 'f1', 'fieldName': 'title', 'label': '标题', 'controlType': 'text',
             'required': False, 'order': 1},
            {'id': 'f2', 'fieldName': 'seq', 'label': '编号', 'controlType': 'autoSequence',
             'required': False, 'order': 2,
             'sequenceConfig': {'prefix': 'PRB-', 'max': 9999}},
        ]
        cur.execute(
            "INSERT INTO page_configs (id, name, description, api_endpoint, fields, "
            "  created_at, updated_at, row_actions) "
            "VALUES (%s,%s,%s,%s,%s,NOW(),NOW(),%s)",
            (page_id, 'MCP写探针', '内部转发测试页', f'/{collection}',
             json.dumps(fields), '[]'))
        project_menu_id = f'menu-proj-{suffix}'
        cur.execute(
            "INSERT INTO menus (id, name, icon, page_id, parent_id, \"order\", "
            "  path, roles, menu_type) "
            "VALUES (%s,%s,'Folder',NULL,NULL,1,%s,'[]','project')",
            (project_menu_id, f'探针项目-{suffix}', '/proj-' + suffix))
    db_conn.commit()

    from app import app
    app.config['TESTING'] = True
    yield {'client': app.test_client(), 'uid': uid, 'collection': collection,
           'page_id': page_id, 'project_menu_id': project_menu_id}

    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM operation_logs WHERE operator_id = %s", (uid,))
        cur.execute("DELETE FROM dynamic_data WHERE collection = %s", (collection,))
        cur.execute("DELETE FROM menus WHERE page_id = %s", (page_id,))
        cur.execute("DELETE FROM menus WHERE id = %s", (project_menu_id,))
        cur.execute("DELETE FROM page_configs WHERE id = %s", (page_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def test_requires_internal_token(setup_app):
    f = setup_app
    r = f['client'].post('/ai/data-internal/execute', json={}, headers={})
    assert r.status_code == 403
    r = _execute(f['client'], {'userId': f['uid'], 'method': 'POST', 'path': '/x'},
                 headers={'X-Internal-Token': 'wrong-token'})
    assert r.status_code == 403


def test_forward_allowlist_rejects_other_paths(setup_app):
    f = setup_app
    for method, path in (('GET', f"/{f['collection']}"), ('POST', '/users'),
                         ('POST', '/ai/chat/batches'), ('DELETE', '/menus/abc'),
                         ('POST', '/pageConfigs'), ('POST', '/operationLogs'),
                         ('POST', f"/{f['collection']}/r1/row-actions/x/run")):
        r = _execute(f['client'], {'userId': f['uid'], 'method': method, 'path': path})
        assert r.status_code == 403, (method, path, r.get_data(as_text=True))
    # 查询串/路径穿越是参数非法(400),同样不允许
    r = _execute(f['client'], {'userId': f['uid'], 'method': 'POST',
                               'path': f"/{f['collection']}?x=1"})
    assert r.status_code == 400


def test_unknown_user_rejected(setup_app):
    f = setup_app
    r = _execute(f['client'], {'userId': 'no-such-user', 'method': 'POST',
                               'path': f"/{f['collection']}"})
    assert r.status_code == 403
    assert '用户不存在' in r.get_json()['error']


def test_create_record_full_app_semantics(setup_app, db_conn):
    """创建走真实路由:201 + autoSequence 服务端分配 + 操作日志以该用户留痕。"""
    f = setup_app
    rid = f'r-{uuid.uuid4().hex[:8]}'
    r = _execute(f['client'], {
        'userId': f['uid'], 'method': 'POST', 'path': f"/{f['collection']}",
        'body': {'id': rid, 'title': '探针记录', 'seq': 'HACKED'}})
    assert r.status_code == 200, r.get_data(as_text=True)
    out = r.get_json()
    assert out['status'] == 201
    assert out['body']['id'] == rid
    with db_conn.cursor() as cur:
        cur.execute("SELECT data->>'seq', data->>'title' FROM dynamic_data "
                    "WHERE collection = %s AND id = %s", (f['collection'], rid))
        seq, title = cur.fetchone()
        assert seq.startswith('PRB-') and seq != 'HACKED'  # 服务端原子分配
        assert title == '探针记录'
        cur.execute("SELECT operator_id, action, target_type FROM operation_logs "
                    "WHERE target_id = %s AND action = 'create'", (rid,))
        row = cur.fetchone()
        assert row and row[0] == f['uid'] and row[2] == 'dynamic_data'


def test_update_and_optimistic_lock(setup_app, db_conn):
    f = setup_app
    rid = f'r-{uuid.uuid4().hex[:8]}'
    _execute(f['client'], {'userId': f['uid'], 'method': 'POST',
                           'path': f"/{f['collection']}",
                           'body': {'id': rid, 'title': 'v1'}})
    r = _execute(f['client'], {'userId': f['uid'], 'method': 'PUT',
                               'path': f"/{f['collection']}/{rid}",
                               'body': {'title': 'v2', 'id': rid}})
    assert r.get_json()['status'] == 200
    assert r.get_json()['body']['title'] == 'v2'
    # 乐观锁:过期版本 409
    r = _execute(f['client'], {'userId': f['uid'], 'method': 'PUT',
                               'path': f"/{f['collection']}/{rid}",
                               'body': {'title': 'v3', 'id': rid, '_version': 1}})
    assert r.get_json()['status'] == 409
    assert '409' in r.get_json()['body'].get('error', '') or 'version' in str(r.get_json()['body']).lower()


def test_delete_record(setup_app, db_conn):
    f = setup_app
    rid = f'r-{uuid.uuid4().hex[:8]}'
    _execute(f['client'], {'userId': f['uid'], 'method': 'POST',
                           'path': f"/{f['collection']}",
                           'body': {'id': rid, 'title': '待删'}})
    r = _execute(f['client'], {'userId': f['uid'], 'method': 'DELETE',
                               'path': f"/{f['collection']}/{rid}"})
    assert r.get_json()['status'] == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM dynamic_data WHERE collection=%s AND id=%s",
                    (f['collection'], rid))
        assert cur.fetchone() is None


def test_menu_create_via_forward_and_duplicate_name(setup_app, db_conn):
    """数据菜单挂在项目分组下(路由的层级校验生效);重名给友好错误不是裸异常。"""
    f = setup_app
    menu_id = f'menu-{uuid.uuid4().hex[:8]}'
    r = _execute(f['client'], {
        'userId': f['uid'], 'method': 'POST', 'path': '/menus',
        'body': {'id': menu_id, 'name': f'探针页-{menu_id}', 'menuType': 'data',
                 'pageId': f['page_id'], 'parentId': f['project_menu_id'],
                 'path': f"/{f['collection']}", 'roles': ['admin']}})
    assert r.status_code == 200 and r.get_json()['status'] == 201, r.get_data(as_text=True)
    with db_conn.cursor() as cur:
        cur.execute("SELECT page_id, menu_type, parent_id FROM menus WHERE id = %s",
                    (menu_id,))
        pid, mtype, parent = cur.fetchone()
        assert (pid, mtype) == (f['page_id'], 'data') and parent == f['project_menu_id']
    # 重名:路由的友好错误原样透传(400),不是裸 UniqueViolation
    r = _execute(f['client'], {
        'userId': f['uid'], 'method': 'POST', 'path': '/menus',
        'body': {'id': f'menu-{uuid.uuid4().hex[:8]}', 'name': f'探针页-{menu_id}',
                 'menuType': 'data', 'pageId': f['page_id'],
                 'parentId': f['project_menu_id']}})
    assert r.get_json()['status'] == 400
    assert '已被占用' in r.get_json()['body']['error']


def test_permission_enforced_for_limited_user(setup_app):
    """权限与 UI 同路:非超管普通用户对未授权页面 require_page_action 拒绝。"""
    f = setup_app
    import uuid as _u
    guest_id = f'u-dataint-g-{_u.uuid4().hex[:8]}'
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'guest')",
                        (guest_id, 'dataint-' + guest_id, guest_id))
        conn.commit()
    try:
        r = _execute(f['client'], {
            'userId': guest_id, 'method': 'POST',
            'path': f"/{f['collection']}",
            'body': {'id': f'r-{_u.uuid4().hex[:8]}', 'title': 'x'}})
        assert r.get_json()['status'] == 403
        assert '权限不足' in r.get_json()['body']['error']
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (guest_id,))
            conn.commit()
