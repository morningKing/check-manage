# -*- coding: utf-8 -*-
"""治理端点路由测试：权限/200 形状/400 校验/审计落账。数据层整体 monkeypatch
（真库语义归 test_mcp_tool_rules.py）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# 合跑隔离：conftest app 夹具的 get_db 绑定污染（照抄 test_open_api_takeover_link.py）
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


FAKE_VIEW = {'groups': [{'id': 'data-query', 'name': '数据查询', 'order': 1, 'tools': [
    {'name': 'mtg-test-a', 'description': 'd', 'access': 'read',
     'rules': {'admin': True, 'developer': True, 'guest': False, 'kefu-guest': False}}]}],
    'roles': [{'role': 'admin', 'label': '管理员'}]}


@pytest.fixture
def patched_repo(monkeypatch):
    import routes.ai as ai_mod
    monkeypatch.setattr(ai_mod, 'list_tool_governance', lambda: FAKE_VIEW)
    monkeypatch.setattr(ai_mod, 'replace_tool_rules',
                        lambda rules: {'updated': 1, 'diff': 'developer:mtg-test-a off'})


def test_get_requires_admin(app, dev_headers):
    resp = app.test_client().get('/ai/mcp-tools', headers=dev_headers)
    assert resp.status_code == 403


def test_get_ok(app, patched_repo, admin_headers):
    resp = app.test_client().get('/ai/mcp-tools', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['groups'][0]['id'] == 'data-query'


def test_put_ok_logs_operation(app, patched_repo, admin_headers, monkeypatch):
    logged = {}
    import routes.ai as ai_mod
    monkeypatch.setattr(ai_mod, 'log_operation',
                        lambda *a, **k: logged.update(args=a))
    resp = app.test_client().put(
        '/ai/mcp-tool-rules', headers=admin_headers,
        json={'rules': [{'role': 'developer', 'tool_name': 'mtg-test-a', 'allowed': False}]})
    assert resp.status_code == 200
    assert resp.get_json() == {'updated': 1, 'diff': 'developer:mtg-test-a off'}
    assert logged['args'][0] == 'update'
    assert logged['args'][1] == 'ai_mcp_tool_rules'


def test_put_invalid_rules_400(app, patched_repo, admin_headers, monkeypatch):
    import routes.ai as ai_mod
    from utils.mcp_tool_rules import ToolRulesError
    monkeypatch.setattr(ai_mod, 'replace_tool_rules',
                        lambda rules: (_ for _ in ()).throw(ToolRulesError('unknown role')))
    resp = app.test_client().put(
        '/ai/mcp-tool-rules', headers=admin_headers, json={'rules': [{'role': 'x'}]})
    assert resp.status_code == 400
    assert 'unknown role' in resp.get_json()['error']
