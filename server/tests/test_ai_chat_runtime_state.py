"""routes/ai_chat.py runtime_state —— SSE 断线重连的状态收敛判定（此前零测试）。

契约（P0 spec §9.3）：
- 他人会话 → 404 SESSION_NOT_FOUND；
- turnStatus=running 当且仅当：有 OpenCode 会话 && 状态 active && 服务端
  listener 活着（浏览器断开不影响 running 判定）；
- 返回 lastMessageId 供前端增量补齐。
"""
import sys, os
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import app

SESS = ('sess_1', 'user-admin', 'oc_1', 'active', '/tmp/ws')


def _client():
    app.config['TESTING'] = True
    return app.test_client()


def _patched(sess=SESS, fetchone=('msg-9',), listener=True):
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None

    @contextmanager
    def fake():
        yield conn

    def _run():
        return patch('routes.ai_chat.get_db', fake)

    return _run, sess, listener


def _call(sid='sess_1', headers=None, sess=SESS, fetchone=('msg-9',), listener=True):
    run, sess, listener = _patched(sess, fetchone, listener)
    with run(), \
         patch('routes.ai_chat._load_session_for_user', lambda sid, uid: sess), \
         patch('routes.ai_chat.has_listener', lambda sid: listener):
        return _client().get(f'/ai/chat/sessions/{sid}/runtime-state',
                             headers=headers)


def test_runtime_state_running_with_active_listener(admin_headers):
    r = _call(headers=admin_headers, listener=True)
    body = r.get_json()
    assert r.status_code == 200
    assert body['turnStatus'] == 'running'
    assert body['sessionStatus'] == 'active'
    assert body['lastMessageId'] == 'msg-9'
    assert body['error'] is None


def test_runtime_state_idle_without_listener(admin_headers):
    """浏览器关掉 → listener 退出 → 重连后必须收敛到 idle。"""
    body = _call(headers=admin_headers, listener=False).get_json()
    assert body['turnStatus'] == 'idle'


def test_runtime_state_idle_when_no_opencode_session(admin_headers):
    sess = ('sess_1', 'user-admin', None, 'active', '/tmp/ws')
    body = _call(headers=admin_headers, sess=sess).get_json()
    assert body['turnStatus'] == 'idle'
    assert body['opencodeSessionId'] is None


def test_runtime_state_idle_for_closed_session(admin_headers):
    sess = ('sess_1', 'user-admin', 'oc_1', 'closed', '/tmp/ws')
    body = _call(headers=admin_headers, sess=sess).get_json()
    assert body['turnStatus'] == 'idle'


def test_runtime_state_other_users_session_404(dev_headers):
    r = _call(sid='sess_other', headers=dev_headers, sess=None)
    assert r.status_code == 404
    assert r.get_json()['error']['code'] == 'SESSION_NOT_FOUND'


def test_runtime_state_requires_login():
    r = _call()
    assert r.status_code == 401
