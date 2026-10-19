# -*- coding: utf-8 -*-
"""智能写作（suggest-editor）路由测试：真库（casemanage_test）。

覆盖：文档 CRUD + 归属隔离、模板权限门、内部端点 token 门与 state 往返、
access 归属核对（owner/陌生用户/admin 三方）。导入 app 即触发启动迁移块
（幂等），因此内置模板与菜单 seed 在测试库中已就绪。
"""
import base64
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from app import app
from auth import create_token
from config import MCP_INTERNAL_TOKEN
from db import get_db


def _client():
    app.config['TESTING'] = True
    return app.test_client()


@pytest.fixture(scope='module')
def se_users():
    """两个真实用户行：admin（超管语义走角色行）与 developer。"""
    suffix = os.urandom(4).hex()
    admin_id, dev_id = f'se-admin-{suffix}', f'se-dev-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'admin')", (admin_id, 'se-admin-' + suffix, admin_id))
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (dev_id, 'se-dev-' + suffix, dev_id))
        conn.commit()
    yield {'admin': admin_id, 'dev': dev_id}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_document_states WHERE document_id IN "
                        "(SELECT id FROM se_documents WHERE owner_user_id IN (%s,%s))",
                        (admin_id, dev_id))
            cur.execute("DELETE FROM se_documents WHERE owner_user_id IN (%s,%s)",
                        (admin_id, dev_id))
            cur.execute("DELETE FROM se_templates WHERE owner_user_id IN (%s,%s)",
                        (admin_id, dev_id))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (admin_id, dev_id))
        conn.commit()


@pytest.fixture(scope='module')
def dev_client(se_users):
    token = create_token({'id': se_users['dev'], 'username': 'se-dev', 'role': 'developer'})
    return _client(), {'Authorization': f'Bearer {token}'}


@pytest.fixture(scope='module')
def admin_client(se_users):
    token = create_token({'id': se_users['admin'], 'username': 'se-admin', 'role': 'admin'})
    return _client(), {'Authorization': f'Bearer {token}'}


def _internal_headers(token=MCP_INTERNAL_TOKEN or 'test-token'):
    return {'X-Internal-Token': token}


# ------------------------------- 文档 CRUD -------------------------------


def test_document_crud_roundtrip(dev_client):
    client, headers = dev_client
    r = client.post('/suggest-editor/documents', headers=headers,
                    json={'title': '我的周报', 'templateId': 'se-tpl-weekly'})
    assert r.status_code == 201, r.get_data(as_text=True)
    doc = r.get_json()['document']
    assert doc['title'] == '我的周报'
    assert doc['templateId'] == 'se-tpl-weekly'
    assert doc['status'] == 'active'
    doc_id = doc['id']

    # 列表可见
    r = client.get('/suggest-editor/documents', headers=headers)
    ids = [d['id'] for d in r.get_json()['documents']]
    assert doc_id in ids

    # 详情
    r = client.get(f'/suggest-editor/documents/{doc_id}', headers=headers)
    assert r.status_code == 200
    assert r.get_json()['document']['title'] == '我的周报'

    # open 心跳
    r = client.post(f'/suggest-editor/documents/{doc_id}/open', headers=headers)
    assert r.status_code == 200

    # 重命名
    r = client.patch(f'/suggest-editor/documents/{doc_id}', headers=headers,
                     json={'title': '我的周报（改）'})
    assert r.status_code == 200
    assert r.get_json()['document']['title'] == '我的周报（改）'

    # 删除后再查 404，列表也不再出现
    r = client.delete(f'/suggest-editor/documents/{doc_id}', headers=headers)
    assert r.status_code == 200
    assert client.get(f'/suggest-editor/documents/{doc_id}', headers=headers).status_code == 404
    r = client.get('/suggest-editor/documents', headers=headers)
    assert doc_id not in [d['id'] for d in r.get_json()['documents']]


def test_document_owner_isolation(dev_client, admin_client):
    dev_c, dev_headers = dev_client
    admin_c, admin_headers = admin_client

    r = dev_c.post('/suggest-editor/documents', headers=dev_headers, json={'title': 'dev 的私有文档'})
    doc_id = r.get_json()['document']['id']

    # 另一个用户不可见 / 不可改 / 不可删（404 语义，不泄露存在性）。
    # admin 用户行存在但 owner 校验按 userId 判——管理面不提供跨人文档列表，
    # 文档对非属主一律 404，这是刻意收紧。
    assert admin_c.get(f'/suggest-editor/documents/{doc_id}', headers=admin_headers).status_code == 404
    assert dev_c.patch(f'/suggest-editor/documents/{doc_id}', headers=admin_headers,
                       json={'title': 'x'}).status_code == 404

    dev_c.delete(f'/suggest-editor/documents/{doc_id}', headers=dev_headers)


def test_create_document_rejects_unknown_template(dev_client):
    client, headers = dev_client
    r = client.post('/suggest-editor/documents', headers=headers, json={'title': 'x', 'templateId': 'nope'})
    assert r.status_code == 400


def test_documents_require_login():
    r = _client().get('/suggest-editor/documents')
    assert r.status_code == 401


# ------------------------------- 模板 -------------------------------


def test_builtin_templates_seeded(dev_client):
    client, headers = dev_client
    r = client.get('/suggest-editor/templates', headers=headers)
    assert r.status_code == 200
    templates = {t['id']: t for t in r.get_json()['templates']}
    for tpl_id in ('se-tpl-blank', 'se-tpl-weekly', 'se-tpl-meeting', 'se-tpl-proposal'):
        assert tpl_id in templates, f'缺少内置模板 {tpl_id}'
        assert templates[tpl_id]['isBuiltin'] is True
    assert '<h1>' in templates['se-tpl-weekly']['contentHtml']


def test_template_write_requires_admin_permission(dev_client, admin_client, se_users):
    dev_c, dev_headers = dev_client
    admin_c, admin_headers = admin_client

    # developer 无 admin.suggest_editor → 403
    assert dev_c.post('/suggest-editor/templates', headers=dev_headers,
                      json={'name': 'x', 'contentHtml': '<p>x</p>'}).status_code == 403

    r = admin_c.post('/suggest-editor/templates', headers=admin_headers,
                     json={'name': '测试模板', 'description': 'd', 'contentHtml': '<h1>t</h1>'})
    assert r.status_code == 201
    tpl = r.get_json()['template']
    assert tpl['isBuiltin'] is False

    r = admin_c.patch(f"/suggest-editor/templates/{tpl['id']}", headers=admin_headers,
                      json={'name': '测试模板（改）', 'contentHtml': '<h2>t2</h2>'})
    assert r.get_json()['template']['name'] == '测试模板（改）'

    assert admin_c.delete(f"/suggest-editor/templates/{tpl['id']}",
                          headers=admin_headers).status_code == 200
    assert admin_c.delete(f"/suggest-editor/templates/{tpl['id']}",
                          headers=admin_headers).status_code == 404


# ------------------------------- 内部端点 -------------------------------


def test_internal_endpoints_reject_without_token(se_users):
    c = _client()
    assert c.get('/suggest-editor/internal/documents/whatever/state').status_code == 403
    assert c.put('/suggest-editor/internal/documents/whatever/state',
                 json={'state': 'AAAA', 'analyzedHashes': []}).status_code == 403
    assert c.get('/suggest-editor/internal/documents/whatever/access?userId=u').status_code == 403
    # 错 token 同样 403
    assert c.get('/suggest-editor/internal/documents/whatever/state',
                 headers=_internal_headers('wrong')).status_code == 403


def test_internal_state_roundtrip(se_users, dev_client):
    client, headers = dev_client
    r = client.post('/suggest-editor/documents', headers=headers, json={'title': '状态往返'})
    doc_id = r.get_json()['document']['id']

    payload = {'state': base64.b64encode(b'\x01\x02\x03yjs-state').decode('ascii'),
               'analyzedHashes': ['hash1', 'hash2']}
    c = _client()
    assert c.put(f'/suggest-editor/internal/documents/{doc_id}/state',
                 headers=_internal_headers(), json=payload).status_code == 200

    r = c.get(f'/suggest-editor/internal/documents/{doc_id}/state', headers=_internal_headers())
    body = r.get_json()
    assert base64.b64decode(body['state']) == b'\x01\x02\x03yjs-state'
    assert body['analyzedHashes'] == ['hash1', 'hash2']

    # PUT 覆盖语义
    assert c.put(f'/suggest-editor/internal/documents/{doc_id}/state',
                 headers=_internal_headers(),
                 json={'state': base64.b64encode(b'v2').decode('ascii'),
                       'analyzedHashes': []}).status_code == 200
    body = c.get(f'/suggest-editor/internal/documents/{doc_id}/state',
                 headers=_internal_headers()).get_json()
    assert base64.b64decode(body['state']) == b'v2'

    # 文档删除后 PUT 404（不 resurrect）
    client.delete(f'/suggest-editor/documents/{doc_id}', headers=headers)
    assert c.put(f'/suggest-editor/internal/documents/{doc_id}/state',
                 headers=_internal_headers(),
                 json={'state': base64.b64encode(b'v3').decode('ascii'),
                       'analyzedHashes': []}).status_code == 404


def test_internal_access_check_owner_stranger_admin(se_users, dev_client):
    dev_c, dev_headers = dev_client
    r = dev_c.post('/suggest-editor/documents', headers=dev_headers, json={'title': '归属核对'})
    doc_id = r.get_json()['document']['id']
    c = _client()

    base = f'/suggest-editor/internal/documents/{doc_id}/access'
    # 属主 → allowed
    assert c.get(f'{base}?userId={se_users["dev"]}', headers=_internal_headers()).get_json() == \
        {'exists': True, 'allowed': True}
    # 陌生用户 → 拒绝
    assert c.get(f'{base}?userId=stranger', headers=_internal_headers()).get_json() == \
        {'exists': True, 'allowed': False}
    # admin 角色用户也无旁路（写作文档私有，与 REST 404 语义一致）
    assert c.get(f'{base}?userId={se_users["admin"]}', headers=_internal_headers()).get_json() == \
        {'exists': True, 'allowed': False}

    # 文档删除后 → exists=False
    dev_c.delete(f'/suggest-editor/documents/{doc_id}', headers=dev_headers)
    assert c.get(f'{base}?userId={se_users["dev"]}', headers=_internal_headers()).get_json() == \
        {'exists': False, 'allowed': False}


def test_internal_state_put_validates_payload(se_users, dev_client):
    client, headers = dev_client
    r = client.post('/suggest-editor/documents', headers=headers, json={'title': '载荷校验'})
    doc_id = r.get_json()['document']['id']
    c = _client()
    url = f'/suggest-editor/internal/documents/{doc_id}/state'
    assert c.put(url, headers=_internal_headers(), json={'analyzedHashes': []}).status_code == 400
    assert c.put(url, headers=_internal_headers(),
                 json={'state': 'not-base64!!', 'analyzedHashes': []}).status_code == 400
    assert c.put(url, headers=_internal_headers(),
                 json={'state': 'AAAA', 'analyzedHashes': 'oops'}).status_code == 400
    client.delete(f'/suggest-editor/documents/{doc_id}', headers=headers)


def test_internal_settings_exposes_platform_default_model(se_users):
    """Node 启动引导读取的平台默认模型：token 门 + 形状（值来自 ai_settings）。"""
    c = _client()
    assert c.get('/suggest-editor/internal/settings').status_code == 403
    r = c.get('/suggest-editor/internal/settings', headers=_internal_headers())
    assert r.status_code == 200
    body = r.get_json()
    assert 'defaultModel' in body
    # 与平台同一取数函数（DB 优先、env 兜底）——两边读到的必须是同一个值
    from config import get_default_chat_model
    assert body['defaultModel'] == get_default_chat_model()
