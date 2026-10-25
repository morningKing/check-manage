"""对外公开接管蓝图 /external/takeover/*（无 JWT，接管令牌 + 双层限速）。

模式照抄 test_open_api_takeover_link.py：认证网关
（takeover_repo.resolve_takeover_token）整体打桩；batch_repo /
batch_engine.get_worker / operation_log._write_log_row 打桩做行为隔离；
限速走真 RateLimiter——每测换新实例 + 调低阈值，确定性无睡眠。
"""
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routes import external_takeover as ext  # noqa: E402  (RED: 模块尚不存在)

BASE = '/external/takeover'
VALID_T = 'tk_ok'


def _tok(**over):
    row = {
        'token_id': 'tkt_1',
        'owner_user_id': 'user-42',
        'batch_id': 'b1',
        'session_id': 's1',
        'batch_seq': 3,
        'batch_input_file': 'input.xlsx',
        'batch_name': 'B1',
        'title': '第3步',
        'child_status': 'completed',
        'batch_status': 'running',
        'expires_at': datetime.now(timezone.utc) + timedelta(hours=24),
    }
    row.update(over)
    return row


@contextmanager
def _fake_db(mock_conn):
    yield mock_conn


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    """每测换新限速桶：用例间互不串桶，确定性无睡眠。"""
    from utils.rate_limit import RateLimiter
    monkeypatch.setattr(ext, '_limiter', RateLimiter())


@pytest.fixture(autouse=True)
def _cursor_enter_self(mock_conn):
    """`with conn.cursor() as cur` 在裸 MagicMock 下绑定的是
    __enter__.return_value（新造的自动 mock），conftest 配置的 fetchone
    根本到不了被测代码——把 __enter__ 指回同一个 mock_cursor。"""
    cur = mock_conn.cursor.return_value
    cur.__enter__.return_value = cur


def _owner_row():
    """_log_takeover 里 SELECT username, role FROM users 的返回。"""
    return ('alice', 'admin')


def test_invalid_token_404_no_store(client, mock_cursor):
    """令牌缺失/空/伪造一律同一 404 体（resolve 桩返回 None 即覆盖
    过期/吊销——蓝图侧不得产生存在性预言机）。"""
    for qs in ('', '?t=', f'?t={VALID_T}'):
        mock_cursor.reset_mock()
        with patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
                   return_value=None) as rt:
            resp = client.get(f'{BASE}/session{qs}')
        assert resp.status_code == 404
        assert resp.get_json() == {'error': '链接无效或已过期'}
        assert resp.headers['Cache-Control'] == 'no-store'
        assert rt.call_args.args == ({'': None, '?t=': ''}.get(qs, VALID_T),)


def test_session_info_field_shape(client, mock_conn, mock_cursor):
    tok = _tok()
    with patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
               return_value=tok), \
         patch('routes.external_takeover.get_db', lambda: _fake_db(mock_conn)):
        resp = client.get(f'{BASE}/session?t={VALID_T}')
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {'batchName', 'batchSeq', 'inputFile', 'title',
                         'childStatus', 'batchStatus', 'expiresAt'}
    assert body == {
        'batchName': 'B1', 'batchSeq': 3, 'inputFile': 'input.xlsx',
        'title': '第3步', 'childStatus': 'completed', 'batchStatus': 'running',
        'expiresAt': tok['expires_at'].isoformat(),
    }


def test_messages_passthrough_four_fields(client, mock_conn, mock_cursor):
    """行只透传 id/role/content/createdAt（createdAt ISO 化），多余字段丢弃；
    childStatus 现读 DB 而非 resolve 时快照；total/truncated 原样。"""
    dt = datetime(2026, 10, 25, 12, 0, 0)
    msgs = {'messages': [
        {'id': 'm1', 'role': 'user', 'content': 'hi', 'created_at': dt,
         'seq': 7},
        {'id': 'm2', 'role': 'assistant', 'content': 'yo', 'created_at': None,
         'seq': 8},
    ], 'truncated': True, 'total': 9}
    mock_cursor.fetchone.return_value = ('needs_review',)
    with patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
               return_value=_tok()), \
         patch('routes.external_takeover.get_db', lambda: _fake_db(mock_conn)), \
         patch('routes.external_takeover.batch_repo.get_child_messages',
               return_value=msgs) as gm:
        resp = client.get(f'{BASE}/session/messages?t={VALID_T}')
    assert resp.status_code == 200
    body = resp.get_json()
    assert gm.call_args.args == ('s1',)
    assert body['childStatus'] == 'needs_review'
    assert body['total'] == 9 and body['truncated'] is True
    assert body['messages'][0] == {'id': 'm1', 'role': 'user', 'content': 'hi',
                                   'createdAt': dt.isoformat()}
    assert body['messages'][1]['createdAt'] is None
    for m in body['messages']:
        assert set(m) == {'id', 'role', 'content', 'createdAt'}


def _send_patches(mock_conn, tok, status, owner=None):
    """发送端点的公共打桩：resolve 放行、_child_status_fresh 读到 `status`、
    _log_takeover 的属主查询读到 owner（默认 alice/admin）。"""
    owner = owner or _owner_row()
    mock_cursor = mock_conn.cursor.return_value
    mock_cursor.fetchone.side_effect = [(status,), owner]
    return (
        patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
              return_value=tok),
        patch('routes.external_takeover.get_db', lambda: _fake_db(mock_conn)),
        patch('routes.external_takeover.batch_repo.continue_child'),
        patch('utils.batch_engine.get_worker', return_value=MagicMock()),
        patch('utils.operation_log._write_log_row'),
    )


def test_send_running_409_continue_not_called(client, mock_conn, mock_cursor):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'running')
    with p_tok, p_db, p_cc as cc, p_gw as gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 409
    assert resp.get_json() == {
        'error': {'code': 'TASK_RUNNING',
                  'message': '任务执行中，请外部系统先暂停后再接管'}}
    cc.assert_not_called()
    gw.assert_not_called()


def test_send_pending_409(client, mock_conn, mock_cursor):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), 'pending')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 409
    cc.assert_not_called()


def test_send_paused_repark_true_and_notify(client, mock_conn, mock_cursor):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), 'paused')
    with p_tok, p_db, p_cc as cc, p_gw as gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 202
    assert resp.get_json() == {'queued': True}
    assert cc.call_args.args == ('user-42', 'b1', 's1', '接着做')
    assert cc.call_args.kwargs == {'repark': True}
    gw.assert_called_once_with()
    gw.return_value.notify.assert_called_once_with()


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled',
                                    'needs_review'])
def test_send_terminal_repark_false(client, mock_conn, mock_cursor, status):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), status)
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 202
    assert cc.call_args.kwargs == {'repark': False}


def test_send_unknown_status_404(client, mock_conn, mock_cursor):
    """会话已消失（重读为空）→ 与无效令牌同一 404 体。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), '')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 404
    assert resp.get_json() == {'error': '链接无效或已过期'}
    cc.assert_not_called()


def test_send_state_conflict_409(client, mock_conn, mock_cursor):
    """continue_child 内部 CAS 失败（ValueError）→ 409 STATE_CONFLICT。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), 'paused')
    with p_tok, p_db, p_cc as cc, p_gw as gw, p_wl:
        cc.side_effect = ValueError('子任务状态已变化，请刷新后重试')
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 409
    assert resp.get_json()['error']['code'] == 'STATE_CONFLICT'
    gw.return_value.notify.assert_not_called()


@pytest.mark.parametrize('payload', [{}, {'prompt': ''}, {'prompt': '   '}])
def test_send_prompt_empty_400(client, mock_conn, mock_cursor, payload):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'completed')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}', json=payload)
    assert resp.status_code == 400
    cc.assert_not_called()


def test_send_prompt_too_long_400_and_boundary_ok(client, mock_conn,
                                                  mock_cursor):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'completed')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': 'x' * 20001})
        assert resp.status_code == 400
        cc.assert_not_called()
        # 边界：恰好 20000 字符放行
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': 'x' * 20000})
    assert resp.status_code == 202


def test_send_rate_limited_second_hit_429(client, mock_conn, mock_cursor,
                                          monkeypatch):
    """真 RateLimiter：阈值调到 1/min，第二击 429（同令牌同 IP）。"""
    monkeypatch.setattr(ext, 'SEND_PER_MINUTE', 1)
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'completed')
    with p_tok, p_db, p_cc, p_gw, p_wl:
        first = client.post(f'{BASE}/session/messages?t={VALID_T}',
                            json={'prompt': '第一击'})
        second = client.post(f'{BASE}/session/messages?t={VALID_T}',
                             json={'prompt': '第二击'})
    assert first.status_code == 202
    assert second.status_code == 429
    assert second.get_json() == {'error': '请求过于频繁，请稍后再试'}
    assert second.headers['Cache-Control'] == 'no-store'


def test_log_takeover_writes_with_owner_identity(client, mock_conn,
                                                 mock_cursor):
    """发消息以令牌属主身份落 oplog：_write_log_row 收到
    (owner_user_id, username, role, action, 'ai_chat_session', session_id, ...)。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'paused')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl as wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 202
    assert wl.call_args.args[:7] == (
        'user-42', 'alice', 'admin', 'ai.batch_takeover_message',
        'ai_chat_session', 's1', '第3步')
    assert 'prev_status=paused' in wl.call_args.args[7]
    assert '接管对话' in wl.call_args.args[7]
