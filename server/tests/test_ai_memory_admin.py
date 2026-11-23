"""记忆管理管理面端点（/ai/chat/admin/memory/*）的测试。

权限门与 test_ai_batch_admin_routes.py 同思路：**参数化遍历** url_map 里本蓝图
全部规则，任何新端点漏挂 require_permission 立刻红。条目删除沿用
open_api_memories.py 的 owned-guard 防线（utils.memory.delete_memory 不按
user_id 过滤，管理员端点同样必须先 list 建集合确认归属再删）。

分层：权限/契约/合并/条目操作用 mock（patch 配置函数与 _owner_row 纯函数）；
用户解析的真实 DB 行为只打一个 _owner_row 直调用例——client 夹具的 db mock
与真库在同一个 HTTP 请求生命周期里混跑会出现跨请求绑定漂移，不在请求路径上混。
"""
import os
import sys
import uuid
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/ai/chat/admin/memory'


@pytest.fixture(autouse=True)
def _heal_leaked_get_db_bindings():
    import db as _db

    def _heal():
        real = _db.get_db
        for name, mod in list(sys.modules.items()):
            if mod is None or not (name.startswith(('routes.', 'utils.'))
                                   or name in ('auth', 'app')):
                continue
            for attr in ('get_db', '_default', '_default_get_db'):
                cur = getattr(mod, attr, None)
                if cur is not None and cur is not real and callable(cur):
                    try:
                        setattr(mod, attr, real)
                    except (AttributeError, TypeError):
                        pass
        if hasattr(_db.pool, '_mock_name'):
            _db.pool = None

    _heal()
    yield
    _heal()


def _real_conn_factory(db_conn):
    """把路由模块层的 get_db 指到真库连接（直调 _owner_row 的用例用）。"""
    from contextlib import contextmanager as _cm

    @_cm
    def _f():
        yield db_conn

    return _f


def _admin_routes(app):
    """url_map 里本蓝图的全部规则 → (method, url)（路径参数填占位值）。"""
    out = []
    for rule in app.url_map.iter_rules():
        if not str(rule).startswith(BASE):
            continue
        url = str(rule).replace('<memory_id>', 'mid-x')
        for m in (rule.methods or set()) & {'GET', 'POST', 'PUT', 'DELETE'}:
            out.append((m, url))
    return sorted(out)


def test_blueprint_exposes_expected_routes(app):
    routes = _admin_routes(app)
    for m, u in [('GET', BASE), ('PUT', BASE), ('GET', f'{BASE}/users'),
                 ('GET', f'{BASE}/entries'), ('DELETE', f'{BASE}/entries/mid-x'),
                 ('POST', f'{BASE}/test/search'), ('POST', f'{BASE}/test/add')]:
        assert (m, u) in routes, (m, u)


@pytest.mark.parametrize('method,url', [
    (m, u) for m, u in [
        ('GET', BASE), ('PUT', BASE), ('GET', f'{BASE}/users'),
        ('GET', f'{BASE}/entries'), ('DELETE', f'{BASE}/entries/mid-x'),
        ('POST', f'{BASE}/test/search'), ('POST', f'{BASE}/test/add'),
    ]
])
def test_routes_require_admin_capability(client, method, url):
    """无 token 一律不放行，绝不能 200。"""
    resp = client.open(url, method=method)
    assert resp.status_code in (401, 403)


def test_every_route_rejects_authenticated_non_admin(app, client, dev_headers):
    """本文件真正的安全边界：已登录但无 admin.ai_settings 的角色逐端点 403。"""
    leaked = []
    for method, url in _admin_routes(app):
        resp = client.open(url, method=method, headers=dev_headers)
        if resp.status_code != 403:
            leaked.append((method, url, resp.status_code))
    assert leaked == []


# ── GET /config：契约 + 状态 ──────────────────────────────────────────────

def test_config_returns_memory_fields_and_status(client, admin_headers, tmp_path):
    # 假密钥用拼接构造（Mimosa 对 sk- 开头的字面量凭据会拦）
    fake_key = 'sk-' + 'demo' * 4 + '-tail'
    settings = {'mem0Enabled': True, 'embeddingProvider': 'ollama',
                'embeddingModel': 'nomic-embed-text',
                'embeddingOllamaUrl': 'http://localhost:11434',
                'embeddingDims': 1024, 'endpoint': 'https://api.example.com/v1',
                'model': 'glm-4', 'apiKey': fake_key}
    (tmp_path / 'store').mkdir()
    fake_mem = object()
    with patch('routes.ai_memory_admin.get_ai_settings', return_value=settings), \
         patch('routes.ai_memory_admin.get_memory', return_value=fake_mem), \
         patch('routes.ai_memory_admin.MEM0_STORE_ROOT', str(tmp_path / 'store')):
        resp = client.get(BASE, headers=admin_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['mem0Enabled'] is True
    assert body['embeddingProvider'] == 'ollama'
    assert body['embeddingDims'] == 1024
    assert body['model'] == 'glm-4'
    assert body['apiKey'].startswith('*')          # 掩码
    assert fake_key not in body['apiKey']          # 明文不泄漏
    assert body['status']['ready'] is True
    assert body['status']['storePath'] == str(tmp_path / 'store')
    assert body['status']['storeExists'] is True


def test_config_status_not_ready_when_disabled(client, admin_headers):
    settings = {'mem0Enabled': False, 'embeddingProvider': 'api',
                'embeddingModel': 'text-embedding-v3',
                'embeddingOllamaUrl': 'http://localhost:11434',
                'embeddingDims': 1024, 'endpoint': 'e', 'model': 'm',
                'apiKey': ''}
    with patch('routes.ai_memory_admin.get_ai_settings', return_value=settings), \
         patch('routes.ai_memory_admin.get_memory', return_value=None):
        resp = client.get(BASE, headers=admin_headers)
    assert resp.get_json()['status']['ready'] is False


# ── PUT /config：read-merge 合并 + 单例重置 ─────────────────────────────

def _current_settings():
    return {'enabled': True,
            'apiKey': 'sk-' + 'real' * 4 + '-value',
            'endpoint': 'https://api.example.com/v1',
            'model': 'glm-4', 'timeout': 30, 'maxTokens': 1024,
            'mem0Enabled': True, 'embeddingProvider': 'api',
            'embeddingModel': 'text-embedding-v3',
            'embeddingOllamaUrl': 'http://localhost:11434',
            'embeddingDims': 1024, 'defaultChatModel': '', 'maxBatchSessions': 50}


def test_config_put_merges_with_current(client, admin_headers):
    """只带记忆字段的请求不得动模型页字段（enabled/apiKey/endpoint 等原样保留），
    且必须触发 reset_memory_singleton（配置变更后按新配置重建）。"""
    with patch('routes.ai_memory_admin.get_ai_settings',
               return_value=_current_settings()), \
         patch('routes.ai_memory_admin.update_ai_settings',
               return_value=_current_settings()) as upd, \
         patch('routes.ai_memory_admin.reset_memory_singleton') as reset, \
         patch('routes.ai_memory_admin.log_operation'):
        resp = client.put(BASE, headers=admin_headers,
                          json={'mem0Enabled': False, 'embeddingDims': 768})
    assert resp.status_code == 200
    kw = upd.call_args[1]
    assert kw['mem0_enabled'] is False
    assert kw['embedding_dims'] == 768
    # 未覆盖字段回填当前值（模型页配置不被误清）
    assert kw['enabled'] is True
    assert kw['api_key'] == 'sk-' + 'real' * 4 + '-value'
    assert kw['endpoint'] == 'https://api.example.com/v1'
    assert kw['embedding_provider'] == 'api'
    reset.assert_called_once()


def test_config_put_validates_provider_and_dims(client, admin_headers):
    with patch('routes.ai_memory_admin.get_ai_settings',
               return_value=_current_settings()), \
         patch('routes.ai_memory_admin.update_ai_settings') as upd:
        resp = client.put(BASE, headers=admin_headers,
                          json={'embeddingProvider': 'vertex'})
        assert resp.status_code == 400
        resp = client.put(BASE, headers=admin_headers,
                          json={'embeddingDims': 16})
        assert resp.status_code == 400
    upd.assert_not_called()


# ── GET /users 与 GET /entries：用户解析（真库直调 + 路由契约分层）────────

@pytest.fixture
def pick_user(db_conn):
    uid = 'u-memadm-' + uuid.uuid4().hex[:8]
    username = f'alice-{uid[-8:]}'
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s,%s,'x',%s,'developer')", (uid, username, uid))
    db_conn.commit()
    yield {'id': uid, 'username': username}
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    db_conn.commit()


def test_owner_row_resolves_username_id_and_missing(db_conn, pick_user):
    """_owner_row 直调真库：用户名/id 双解析 + 不存在返回 None（单次调用，
    不经 HTTP——client 夹具的 db mock 与真库在请求路径上混跑会出现绑定漂移）。"""
    from routes.ai_memory_admin import _owner_row
    with patch('routes.ai_memory_admin.get_db', _real_conn_factory(db_conn)):
        assert _owner_row(pick_user['username']) == (pick_user['id'],
                                                     pick_user['username'])
        assert _owner_row(pick_user['id']) == (pick_user['id'],
                                               pick_user['username'])
        assert _owner_row('no-such-user-xyz') is None


def test_users_search_matches_by_prefix(client, admin_headers, db_conn,
                                        pick_user):
    with patch('routes.ai_memory_admin.get_db', _real_conn_factory(db_conn)):
        resp = client.get(f'{BASE}/users?q={pick_user["username"][:10]}',
                          headers=admin_headers)
    assert resp.status_code == 200
    users = resp.get_json()['users']
    assert any(u['id'] == pick_user['id'] for u in users)


def test_entries_resolve_username_and_list(client, admin_headers):
    """owner 用户名已被解析成 userId 传给 list_memories（mem0 按 user_id 隔离，
    页面不让管理员手输内部 id）；解析失败 → 404 OWNER_NOT_FOUND。"""
    mems = [{'id': 'm1', 'memory': '喜欢 Python', 'created_at': None}]
    with patch('routes.ai_memory_admin._owner_row',
               return_value=('u-x', 'alice')), \
         patch('routes.ai_memory_admin.list_memories', return_value=mems) as lm:
        resp = client.get(f'{BASE}/entries?owner=alice', headers=admin_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['memories'] == mems
    assert body['ownerUserId'] == 'u-x'
    assert body['ownerUsername'] == 'alice'
    lm.assert_called_once_with('u-x')

    with patch('routes.ai_memory_admin._owner_row', return_value=None):
        resp = client.get(f'{BASE}/entries?owner=no-such-user-xyz',
                          headers=admin_headers)
    assert resp.status_code == 404
    assert resp.get_json()['code'] == 'OWNER_NOT_FOUND'


def test_entries_query_uses_search(client, admin_headers):
    with patch('routes.ai_memory_admin._owner_row',
               return_value=('u-x', 'alice')), \
         patch('routes.ai_memory_admin.search_memory', return_value=[]) as sm:
        resp = client.get(f'{BASE}/entries?owner=alice&query=python&limit=3',
                          headers=admin_headers)
    assert resp.status_code == 200
    sm.assert_called_once_with('u-x', 'python', 3)


# ── DELETE /entries/<id>：owned-guard（IDOR 防线）────────────────────────

def test_delete_entries_rejects_unowned_id(client, admin_headers):
    """utils.memory.delete_memory 不做归属校验——管理员端点与开放 API 同样
    必须先确认目标 id 在该用户集合里，否则任意 memoryId 可删别人记忆。"""
    with patch('routes.ai_memory_admin._owner_row',
               return_value=('u-x', 'alice')), \
         patch('routes.ai_memory_admin.list_memories',
               return_value=[{'id': 'm-other'}]), \
         patch('routes.ai_memory_admin.delete_memory') as dm:
        resp = client.delete(f'{BASE}/entries/mid-nope?owner=alice',
                             headers=admin_headers)
    assert resp.status_code == 404
    dm.assert_not_called()


def test_delete_entries_owned_ok(client, admin_headers):
    with patch('routes.ai_memory_admin._owner_row',
               return_value=('u-x', 'alice')), \
         patch('routes.ai_memory_admin.list_memories',
               return_value=[{'id': 'm-own'}]), \
         patch('routes.ai_memory_admin.delete_memory') as dm, \
         patch('routes.ai_memory_admin.log_operation') as lg:
        resp = client.delete(f'{BASE}/entries/m-own?owner=alice',
                             headers=admin_headers)
    assert resp.status_code == 200
    dm.assert_called_once_with('m-own')
    lg.assert_called_once()


# ── POST /test/search 与 /test/add：测试台 ───────────────────────────────

def test_test_search_returns_rendered_block(client, admin_headers):
    results = [{'id': 'm1', 'memory': '喜欢 Python'}, {'id': 'm2', 'memory': '  '}]
    with patch('routes.ai_memory_admin._owner_row',
               return_value=('u-x', 'alice')), \
         patch('routes.ai_memory_admin.search_memory', return_value=results):
        resp = client.post(f'{BASE}/test/search', headers=admin_headers,
                           json={'owner': 'alice', 'query': 'python', 'limit': 5})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['results'] == results
    # rendered 是真实 render_memory_block 的输出——注入提示词原文所见即所得
    assert '[关于当前用户的长期记忆' in body['rendered']
    assert '- 喜欢 Python' in body['rendered']


def test_test_search_empty_query_400(client, admin_headers):
    resp = client.post(f'{BASE}/test/search', headers=admin_headers,
                       json={'owner': 'someone', 'query': '  '})
    assert resp.status_code == 400


def test_test_add_passes_verbatim_and_source(client, admin_headers):
    with patch('routes.ai_memory_admin._owner_row',
               return_value=('u-x', 'alice')), \
         patch('routes.ai_memory_admin.get_memory', return_value=object()), \
         patch('routes.ai_memory_admin.add_memory_text', return_value=True) as am, \
         patch('routes.ai_memory_admin.list_memories', return_value=[]):
        resp = client.post(f'{BASE}/test/add', headers=admin_headers,
                           json={'owner': 'alice', 'text': '偏好表格输出',
                                 'verbatim': True})
    assert resp.status_code == 200
    kw = am.call_args[1]
    assert kw['infer'] is False            # verbatim → 不提炼
    assert kw['source'] == 'manual'


def test_test_add_unavailable_memory_409(client, admin_headers):
    """mem0 未配置/初始化失败（get_memory None 或写入降级 False）→
    409 MEMORY_UNAVAILABLE，与用户侧端点同契约。"""
    with patch('routes.ai_memory_admin._owner_row',
               return_value=('u-x', 'alice')), \
         patch('routes.ai_memory_admin.get_memory', return_value=None):
        resp = client.post(f'{BASE}/test/add', headers=admin_headers,
                           json={'owner': 'alice', 'text': 'x'})
    assert resp.status_code == 409
    assert resp.get_json()['code'] == 'MEMORY_UNAVAILABLE'

    with patch('routes.ai_memory_admin._owner_row',
               return_value=('u-x', 'alice')), \
         patch('routes.ai_memory_admin.get_memory', return_value=object()), \
         patch('routes.ai_memory_admin.add_memory_text', return_value=False):
        resp = client.post(f'{BASE}/test/add', headers=admin_headers,
                           json={'owner': 'alice', 'text': 'x'})
    assert resp.status_code == 409
    assert resp.get_json()['code'] == 'MEMORY_UNAVAILABLE'


def test_test_add_empty_text_400(client, admin_headers):
    resp = client.post(f'{BASE}/test/add', headers=admin_headers,
                       json={'owner': 'someone', 'text': '  '})
    assert resp.status_code == 400
