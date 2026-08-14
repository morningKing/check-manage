"""require_permission_sse 装饰器测试：支持 Authorization 头与 ?access_token=
两种取 token 路径；能力门二态由 utils.permissions.can_admin 决定，超管放行。

_token 来源差异（header only / header or query）让两装饰器分道，能力判定
收敛到 auth._user_has_permission 这一条路径——逻辑漂移即安全事故，故用此套
测试覆盖两条入口都走到同一能力门。conftest 把 admin/developer/guest 的 RBAC
缓存预注入了确定性条目，依赖该缓存即可断言超管放行/缺能力 403，无需真实 DB。
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_app():
    from flask import Flask, jsonify, g
    from auth import require_permission_sse

    app = Flask(__name__)
    app.config['TESTING'] = True

    @app.route('/protected')
    @require_permission_sse('admin.ai_chat_admin')
    def protected():
        return jsonify({'ok': True, 'user': g.current_user.get('username')})

    return app


def test_require_permission_sse_no_token_returns_401():
    client = _make_app().test_client()
    r = client.get('/protected')
    assert r.status_code == 401


def test_require_permission_sse_invalid_token_returns_401():
    app = _make_app()
    client = app.test_client()
    with patch('auth.decode_token', return_value=None):
        r = client.get('/protected?access_token=bad')
    assert r.status_code == 401


def test_require_permission_sse_header_token_forwards_to_perm_gate():
    """Authorization 头取 token，能力门放行例 (mock can_admin 返 True)。"""
    app = _make_app()
    client = app.test_client()
    with patch('auth.decode_token', return_value={'userId': 'u', 'username': 'x', 'role': 'custom'}), \
         patch('utils.permissions.can_admin', return_value=True):
        r = client.get('/protected', headers={'Authorization': 'Bearer tok'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True
    assert body['user'] == 'x'


def test_require_permission_sse_query_token_grants_when_capability_held():
    """模拟 <a href=...?access_token=tok> 下载链接：query token + 能力门放行。"""
    app = _make_app()
    client = app.test_client()
    with patch('auth.decode_token', return_value={'userId': 'u', 'username': 'y', 'role': 'custom'}), \
         patch('utils.permissions.can_admin', return_value=True):
        r = client.get('/protected?access_token=tok')
    assert r.status_code == 200
    assert r.get_json()['ok'] is True


def test_require_permission_sse_query_token_denies_when_capability_missing():
    """query token + 能力门拒绝 → 403（浏览器下载链接不能带 Authorization 头）。"""
    app = _make_app()
    client = app.test_client()
    with patch('auth.decode_token', return_value={'userId': 'u', 'username': 'y', 'role': 'custom'}), \
         patch('utils.permissions.can_admin', return_value=False):
        r = client.get('/protected?access_token=tok')
    assert r.status_code == 403


def test_require_permission_sse_superuser_bypasses():
    """admin 角色由 conftest 注入 RBAC 缓存 is_superuser=True，can_admin 直接放行，

    无需 admin_keys 显式列出 admin.ai_chat_admin——这正是 Ruling G 要保留的
    superuser bypass 语义：放行靠 DB 的 roles.is_superuser，不靠 JWT role slug。
    """
    app = _make_app()
    client = app.test_client()
    with patch('auth.decode_token', return_value={'userId': 'u', 'username': 'admin', 'role': 'admin'}):
        r = client.get('/protected?access_token=tok')
    assert r.status_code == 200


def test_require_permission_sse_developer_empty_admin_keys_returns_403():
    """developer 角色：admin_keys 空、非超管（conftest 注入）→ 403。"""
    app = _make_app()
    client = app.test_client()
    with patch('auth.decode_token', return_value={'userId': 'u', 'username': 'dev', 'role': 'developer'}):
        r = client.get('/protected?access_token=tok')
    assert r.status_code == 403


def test_require_permission_sse_guest_denied():
    """guest 角色：非超管、admin_keys 空（conftest 注入）→ 403 (回归)。"""
    app = _make_app()
    client = app.test_client()
    with patch('auth.decode_token', return_value={'userId': 'u', 'username': 'g', 'role': 'guest'}):
        r = client.get('/protected?access_token=tok')
    assert r.status_code == 403