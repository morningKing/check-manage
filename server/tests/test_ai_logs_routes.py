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
    # 全键锁定：Task 3 前端契约（LogTailResponse）依赖这 7 个键一个不多一个不少
    assert set(data) == {'file', 'offset', 'size', 'rotated', 'truncated',
                         'missing', 'lines'}
    assert data['lines'] == ['hello', 'world']
    assert data['file'] == 'chat'
    assert data['missing'] is False


def test_non_admin_denied(client, dev_headers):
    """登录但无 admin.ai_chat_admin：403（区别于匿名的 401）。"""
    resp = client.get(BASE + '?file=chat', headers=dev_headers)
    assert resp.status_code == 403


def test_boundary_params_accepted(client, admin_headers, tmp_path, monkeypatch):
    """边界/缺省值必须被接受（此前只测了拒绝侧）。"""
    p = tmp_path / 'a.log'
    p.write_bytes(b'l1\nl2\n')
    monkeypatch.setitem(LOG_FILES, 'chat', str(p))
    for qs in ('limit=500', 'limit=1', 'offset=-1', ''):   # ''=limit 缺省 300
        resp = client.get(f'{BASE}?file=chat&{qs}', headers=admin_headers)
        assert resp.status_code == 200, qs


def test_chat_log_path_honors_env_override(monkeypatch, tmp_path):
    """chat 路径尊重 AI_CHAT_LOG_FILE（与 logging_setup 写侧同源，防漂移）。"""
    override = str(tmp_path / 'custom.log')
    monkeypatch.setenv('AI_CHAT_LOG_FILE', override)
    assert ai_logs._chat_log_path() == override
    monkeypatch.delenv('AI_CHAT_LOG_FILE')
    assert ai_logs._chat_log_path().endswith('ai-chat.log')


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
