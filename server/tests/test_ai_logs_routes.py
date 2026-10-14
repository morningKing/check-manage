"""GET /ai/logs/tail 路由测试：权限门遍历 + 契约（白名单/参数/形状/缺失态）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routes import ai_logs  # noqa: E402
from routes.ai_logs import LOG_FILES  # noqa: E402

BASE = '/ai/logs/tail'


def test_blueprint_exposes_expected_route(app):
    paths = {str(r) for r in app.url_map.iter_rules()}
    assert BASE in paths


def test_anonymous_rejected(client):
    assert client.get(BASE + '?file=chat').status_code in (401, 403)


def test_every_route_permission_gated(app, client):
    """遍历式兜底：本蓝图任何端点漏挂 require_permission 在这里红。"""
    checked = 0
    for rule in app.url_map.iter_rules():
        if not str(rule).startswith('/ai/logs'):
            continue
        for m in (rule.methods or set()) & {'GET', 'POST'}:
            resp = client.open(str(rule) + '?file=chat', method=m)
            assert resp.status_code in (401, 403), f'{m} {rule} 未挂权限门'
            checked += 1
    assert checked >= 1


def test_admin_reads_file(client, admin_headers, tmp_path, monkeypatch):
    p = tmp_path / 'ai-chat.log'
    p.write_bytes(b'hello\nworld\n')
    monkeypatch.setitem(LOG_FILES, 'chat', str(p))
    resp = client.get(BASE + '?file=chat', headers=admin_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['lines'] == ['hello', 'world']
    assert data['file'] == 'chat'
    assert data['missing'] is False


def test_invalid_file_400(client, admin_headers):
    resp = client.get(BASE + '?file=../secret', headers=admin_headers)
    assert resp.status_code == 400


def test_invalid_params_400(client, admin_headers):
    for qs in ('limit=0', 'limit=501', 'offset=-2', 'offset=abc'):
        resp = client.get(f'{BASE}?file=chat&{qs}', headers=admin_headers)
        assert resp.status_code == 400, qs


def test_missing_file_state(client, admin_headers, tmp_path, monkeypatch):
    monkeypatch.setitem(LOG_FILES, 'serve', str(tmp_path / 'nope.log'))
    resp = client.get(BASE + '?file=serve', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['missing'] is True
