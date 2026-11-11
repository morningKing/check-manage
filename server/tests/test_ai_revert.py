"""utils/ai_revert 纯函数（锚点解析 / 文件能力分级）单测（spec §2.2a）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.ai_revert import resolve_undo_target, file_capability


def _oc(mid, role, created=1000, parts=None):
    return {'info': {'id': mid, 'role': role, 'time': {'created': created}},
            'parts': parts or []}


def _row(rid, role, seq):
    return {'id': rid, 'role': role, 'seq': seq}


def test_anchor_without_later_oc_user_is_pure_platform_undo():
    """锚存在但锚之后无 OC user（该轮从未被后续用户轮接上）→ 纯平台撤销：
    OC 不动、只搬平台行、无文件还原（spec §2.2a 规则⑤）。"""
    # 夹具遵循 spec §2.2a 不变量：平台 assistant 行 id＝该回合第一条 OC
    # assistant 消息 id（msg_a1 即 OC 列表里的锚消息）
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_u2', 'user', 3)]
    oc = [_oc('msg_o1', 'user', 100), _oc('msg_a1', 'assistant', 200)]
    plan = resolve_undo_target(rows, 3, oc)
    assert plan == {'mode': 'native', 'oc_user_message_id': None,
                    'anchor_row_id': 'msg_a1', 'files': []}


def test_target_after_anchor_snaps_to_next_oc_user():
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_u2', 'user', 3)]
    oc = [_oc('msg_o1', 'user', 100), _oc('msg_a1', 'assistant', 150),
          _oc('msg_o3', 'user', 200), _oc('msg_o4', 'assistant', 300)]
    plan = resolve_undo_target(rows, 3, oc)
    assert plan['mode'] == 'native'
    assert plan['anchor_row_id'] == 'msg_a1'
    assert plan['oc_user_message_id'] == 'msg_o3'   # 锚之后第一条 OC user（E2）


def test_files_union_after_oc_target():
    p1 = [{'type': 'patch', 'files': [{'file': 'a.txt'}, {'file': 'b.txt'}]}]
    p2 = [{'type': 'patch', 'files': ['c.txt']}]
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_u2', 'user', 3)]
    oc = [_oc('msg_o1', 'user'), _oc('msg_a1', 'assistant'),
          _oc('msg_o3', 'user'), _oc('msg_o4', 'assistant', parts=p1),
          _oc('msg_o5', 'assistant', parts=p2)]
    plan = resolve_undo_target(rows, 3, oc)
    assert sorted(plan['files']) == ['a.txt', 'b.txt', 'c.txt']


def test_target_is_first_message():
    plan = resolve_undo_target([_row('msg_u1', 'user', 1)], 1,
                               [_oc('msg_o1', 'user', 100)])
    assert plan['oc_user_message_id'] == 'msg_o1'
    assert plan['mode'] == 'native'


def test_anchors_missing_and_oc_empty_is_native_noop():
    """目标前有 assistant 行但全不在 OC、OC 列表空（失败轮从未到达）→ 纯平台撤销。"""
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_u2', 'user', 3)]
    plan = resolve_undo_target(rows, 3, [])
    assert plan == {'mode': 'native', 'oc_user_message_id': None,
                    'anchor_row_id': None, 'files': []}


def test_anchors_missing_and_oc_nonempty_is_rebuild():
    """会话恢复过：平台锚不在新 OC 会话里但 OC 非空 → rebuild（spec §2.2a 规则4）。"""
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_u2', 'user', 3)]
    oc = [_oc('msg_oX', 'user', 100), _oc('msg_oY', 'assistant', 200)]
    plan = resolve_undo_target(rows, 3, oc)
    assert plan['mode'] == 'rebuild'
    assert plan['oc_user_message_id'] is None


def test_failed_turn_between_anchor_and_target_is_skipped():
    """锚与目标之间的失败轮（不在 OC）不阻断：仍取锚之后第一条 OC user。"""
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_ufail', 'user', 3),
            _row('msg_u2', 'user', 4)]
    oc = [_oc('msg_o1', 'user', 100), _oc('msg_a1', 'assistant', 150),
          _oc('msg_o3', 'user', 200)]
    plan = resolve_undo_target(rows, 4, oc)
    assert plan['anchor_row_id'] == 'msg_a1'
    assert plan['oc_user_message_id'] == 'msg_o3'


def test_anchor_after_failed_turn_prefers_latest_live_anchor():
    """最近一条「存在于 OC」的锚赢（失败轮锚被跳过）。"""
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_adead', 'assistant', 3),
            _row('msg_u2', 'user', 4)]
    oc = [_oc('msg_o1', 'user', 100), _oc('msg_a1', 'assistant', 150),
          _oc('msg_o3', 'user', 200)]
    plan = resolve_undo_target(rows, 4, oc)
    assert plan['anchor_row_id'] == 'msg_a1'      # msg_adead 不在 OC → 跳过


def test_capability_none_without_git():
    assert file_capability({'vcs': None, 'worktree': '/'}, '/ws', 1000,
                           ['a.txt']) == 'none'


def test_capability_none_when_worktree_mismatch():
    """嵌在被忽略外层仓库内（E9）：OC worktree ≠ 会话工作区 → 文件不保护。"""
    assert file_capability({'vcs': 'git', 'worktree': '/repo'}, '/repo/sub/ws',
                           1000, ['a.txt']) == 'none'


def test_capability_none_when_no_files():
    assert file_capability({'vcs': 'git', 'worktree': '/ws'}, '/ws', 1000,
                           []) == 'none'


def test_capability_stale_after_max_age():
    now = 200 * 86400 * 1000
    assert file_capability({'vcs': 'git', 'worktree': '/ws'}, '/ws',
                           now - 8 * 86400 * 1000, ['a.txt'],
                           now_ms=now, max_age_days=7) == 'stale'


def test_capability_full():
    now = 200 * 86400 * 1000
    assert file_capability({'vcs': 'git', 'worktree': '/ws'}, '/ws',
                           now - 86400 * 1000, ['a.txt'],
                           now_ms=now, max_age_days=7) == 'full'


# ---------------- SQL 助手（真实 casemanage_test 库，bootstrap 用户） ----------------
import json as _json
import uuid as _uuid

from db import get_db
from utils.ai_revert import (stash_rows, restore_stash, get_revert_state,
                             upsert_revert_state, commit_revert,
                             clear_context_replay)


def _mk_undo_session(sid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = 'admin' LIMIT 1")
        uid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
            "  session_token, token_expires_at) "
            "VALUES (%s, %s, '/tmp/undo-test', 'tok-' || %s, NOW() + interval '1 day') "
            "ON CONFLICT (id) DO NOTHING", (sid, uid, sid))


def _cleanup_undo_session(sid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
        cur.execute("DELETE FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))


def _insert_msg(cur, sid, role, text):
    cur.execute(
        "INSERT INTO ai_chat_messages (id, session_id, role, content) "
        "VALUES (%s, %s, %s, %s)",
        (f'{sid}:{text}', sid, role,
         _json.dumps([{'type': 'text', 'text': text}])))


def test_stash_restore_roundtrip_preserves_seq_and_rating():
    sid = f'sess_undo_{_uuid.uuid4().hex[:8]}'
    try:
        _mk_undo_session(sid)
        with get_db() as conn:
            cur = conn.cursor()
            for role, text in (('user', 'u1'), ('assistant', 'a1'),
                               ('user', 'u2'), ('assistant', 'a2')):
                _insert_msg(cur, sid, role, text)
            cur.execute(
                "SELECT id, seq FROM ai_chat_messages WHERE session_id = %s "
                "ORDER BY seq", (sid,))
            before = cur.fetchall()
            cur.execute(
                "INSERT INTO ai_chat_message_feedback (message_id, session_id, rating) "
                "VALUES (%s, %s, 1) ON CONFLICT (message_id) DO NOTHING",
                (f'{sid}:a1', sid))
            u2_seq = next(s for i, s in before if i.endswith(':u2'))
        with get_db() as conn:
            n = stash_rows(conn, sid, u2_seq)
        assert n == 2
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM ai_chat_messages WHERE session_id = %s",
                        (sid,))
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT count(*) FROM ai_chat_message_stash WHERE session_id = %s",
                        (sid,))
            assert cur.fetchone()[0] == 2
        with get_db() as conn:
            m = restore_stash(conn, sid)
        assert m == 2
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, seq FROM ai_chat_messages WHERE session_id = %s ORDER BY seq",
                (sid,))
            assert cur.fetchall() == before          # seq 保真、顺序不变
            cur.execute("SELECT rating FROM ai_chat_message_feedback "
                        "WHERE message_id = %s", (f'{sid}:a1',))
            assert cur.fetchone()[0] == 1            # rating 随 stash 回插
    finally:
        _cleanup_undo_session(sid)


def test_revert_state_upsert_get_commit():
    sid = f'sess_undo_{_uuid.uuid4().hex[:8]}'
    try:
        _mk_undo_session(sid)
        with get_db() as conn:
            assert get_revert_state(conn, sid) is None
            upsert_revert_state(conn, sid, mode='native',
                                opencode_session_id='oc1',
                                oc_message_id='msg_o3', from_seq=5,
                                message_count=3, file_count=2,
                                file_names=['a.txt', 'b.txt'])
            st = get_revert_state(conn, sid)
            assert st['mode'] == 'native'
            assert st['opencodeSessionId'] == 'oc1'
            assert st['ocMessageId'] == 'msg_o3'
            assert st['fileNames'] == ['a.txt', 'b.txt']
            upsert_revert_state(conn, sid, mode='rebuild',
                                opencode_session_id='oc2', oc_message_id=None,
                                from_seq=5, message_count=3,
                                prev_opencode_session_id='oc1')
            assert get_revert_state(conn, sid)['mode'] == 'rebuild'
            clear_context_replay(conn, sid)
        with get_db() as conn:
            n = commit_revert(conn, sid)
        assert n == 0        # stash 空
        with get_db() as conn:
            assert get_revert_state(conn, sid) is None
    finally:
        _cleanup_undo_session(sid)


# ---------------- 编排器（mock db + mock client，仿 test_routes_ai_chat.setup） ----------------
import time as _time
from contextlib import contextmanager as _cm
from datetime import datetime as _dt, timezone as _tz
from unittest.mock import MagicMock as _MagicMock

import utils.ai_revert as ar


def _mock_env(rows, oc_messages, project=None, revert_info=None, state_rows=None):
    """打桩 get_db/get_runtime/chat_persist；fetchone 按 (ctx, target, state…) 供给。"""
    cursor = _MagicMock()
    cursor.fetchone.side_effect = [
        ('oc1', 'active', '/tmp/ws'),                # _load_undo_context
        (3,),                                        # target seq
    ] + (state_rows or [])
    # fetchall 首个=ctx rows，必须是 DB 元组形态（实现内 r[0] 索引后转 dict）；
    # 其后是 _message_window 等空窗
    cursor.fetchall.side_effect = [
        [(r['id'], r['role'], r['seq']) for r in rows], [], [], [], []]
    conn = _MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda self, *a: False

    @_cm
    def fake_db():
        yield conn

    client = _MagicMock()
    client.get_messages.return_value = oc_messages
    client.get_project.return_value = project or {'vcs': 'git', 'worktree': '/tmp/ws'}
    client.revert_stage.return_value = {'id': 'oc1',
                                        'revert': {'messageID': (revert_info or {}).get(
                                            'messageID', 'msg_o3')}}
    return cursor, client, fake_db


def _two_turn_rows():
    return [{'id': 'msg_a1', 'role': 'assistant', 'seq': 2},
            {'id': 'msg_u2', 'role': 'user', 'seq': 3},
            {'id': 'msg_a2', 'role': 'assistant', 'seq': 4},
            {'id': 'msg_u3', 'role': 'user', 'seq': 5}]


def _oc_anchor_msgs(recent_ms=None):
    """带活锚（msg_a1 在 OC 里）的 OC 夹具：resolve_undo_target 走原生吸附路径。

    msg_o3 是吸附出的 OC user 目标（E2）；msg_o4 携带 patch——files 统计
    只取目标之后的消息，patch 挂在目标自身上会是空集（空 files 直接判
    'none'，到不了 stale 促销）。recent_ms=None 表示 msg_o3 快照远古
    （created=1ms，必 stale）；传具体值则表示新鲜快照（必 full）。
    """
    created = 1 if recent_ms is None else recent_ms
    return [{'info': {'id': 'msg_a1', 'role': 'assistant',
                      'time': {'created': 1}}, 'parts': []},
            {'info': {'id': 'msg_o3', 'role': 'user',
                      'time': {'created': created}}, 'parts': []},
            {'info': {'id': 'msg_o4', 'role': 'assistant',
                      'time': {'created': created + 1000}},
             'parts': [{'type': 'patch', 'files': [{'file': 'a.txt'}]}]}]


def _tracking_db(cursor):
    """每次 with get_db() 生成独立假连接（同一 cursor 贯穿，fetch 序列连续），
    并在生成器层镜像 db.py 的事务语义记录结局：with 体正常退出=commit
    （exc 为 None），体抛异常=rollback（exc 为异常类）。用于断言「收口
    写入真的提交了」——单纯 patch commit_revert 只能证明调用过，证明
    不了没被回滚；而 conn.__exit__ 在 `with get_db()` 下根本不会被调用。"""
    txs = []

    @_cm
    def fake_db():
        conn = _MagicMock()
        conn.cursor.return_value = cursor
        outcome = {'conn': conn, 'exc': None}
        conn.__enter__ = lambda self: conn
        conn.__exit__ = lambda self, *a: False
        txs.append(outcome)
        try:
            yield conn                   # 正常退出 → db.py 在此 commit
        except BaseException as e:
            outcome['exc'] = type(e)     # 体抛异常 → db.py 在此 rollback 后重抛
            raise

    return fake_db, txs


def test_undo_native_calls_revert_stage_with_snapped_user_id(monkeypatch):
    oc = [{'info': {'id': 'msg_a1', 'role': 'assistant', 'time': {'created': 1}}, 'parts': []},
          {'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}},
           'parts': [{'type': 'patch', 'files': [{'file': 'a.txt'}]}]}]
    state = ('native', 'oc1', 'msg_o3', 3, 2, 1, ['a.txt'],
             _dt(2026, 11, 10, tzinfo=_tz.utc), None)
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc,
                                        revert_info={'messageID': 'msg_o3'},
                                        state_rows=[state])
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    monkeypatch.setattr(ar, 'broadcast_revert', lambda sid: None)
    out = ar.undo_from_message('sess_x', 'msg_u2')
    client.revert_stage.assert_called_once()
    args, kwargs = client.revert_stage.call_args
    assert args[0] == 'oc1' and args[1] == 'msg_o3'      # E2 吸附
    assert kwargs['directory'] == '/tmp/ws'
    assert out['revert']['ocMessageId'] == 'msg_o3'
    assert out['deleted'] == 3                            # seq>=3 的平台行数


def test_undo_revert_mismatch_rolls_back(monkeypatch):
    oc = [{'info': {'id': 'msg_a1', 'role': 'assistant', 'time': {'created': 1}}, 'parts': []},
          {'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}}, 'parts': []}]
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc)
    client.revert_stage.return_value = {'id': 'oc1', 'revert': None}   # E6 形态
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    import pytest
    with pytest.raises(ar.UndoError) as ei:
        ar.undo_from_message('sess_x', 'msg_u2')
    assert ei.value.code == 'REVERT_MISMATCH'
    client.revert_clear.assert_not_called()   # 未成功过无需补偿


def test_undo_oc_failure_compensates(monkeypatch):
    """原子性契约：OC revert_stage 成功后平台侧落库失败 → 反向补偿 revert_clear。"""
    oc = [{'info': {'id': 'msg_a1', 'role': 'assistant', 'time': {'created': 1}}, 'parts': []},
          {'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}}, 'parts': []}]
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc)
    import requests as _rq
    boom = _rq.HTTPError('500')
    boom.response = _MagicMock(status_code=500)
    # 失败注入点在 revert_stage 成功之后（upsert_revert_state 抛错）——
    # 若 revert_stage 自身抛错则 oc_reverted=False，补偿分支覆盖不到
    monkeypatch.setattr(ar, 'upsert_revert_state', _MagicMock(side_effect=boom))
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    import pytest
    with pytest.raises(_rq.HTTPError):
        ar.undo_from_message('sess_x', 'msg_u2')
    client.revert_stage.assert_called_once()   # OC 侧已成功撤销
    client.revert_clear.assert_called_once()   # → 平台侧失败触发补偿


def test_undo_oc_409_maps_to_session_busy(monkeypatch):
    """OC revert_stage 回 409（回合进行中）→ SESSION_BUSY，不触发补偿。"""
    oc = [{'info': {'id': 'msg_a1', 'role': 'assistant', 'time': {'created': 1}}, 'parts': []},
          {'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}},
           'parts': [{'type': 'patch', 'files': [{'file': 'a.txt'}]}]}]
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc)
    import requests as _rq
    boom = _rq.HTTPError('409')
    boom.response = _MagicMock(status_code=409)
    client.revert_stage.side_effect = boom
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    import pytest
    with pytest.raises(ar.UndoError) as ei:
        ar.undo_from_message('sess_x', 'msg_u2')
    assert ei.value.code == 'SESSION_BUSY'
    client.revert_clear.assert_not_called()   # OC 撤销未成功，无需补偿


def test_undo_busy_409(monkeypatch):
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: True)
    import pytest
    with pytest.raises(ar.UndoError) as ei:
        ar.undo_from_message('sess_x', 'msg_u2')
    assert ei.value.code == 'SESSION_BUSY'


def test_undo_rebuild_promotes_stale_and_never_calls_oc_revert(monkeypatch):
    oc = _oc_anchor_msgs()          # msg_o3 created=1ms → 必 stale → 促销 rebuild
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc)
    client.create_session.return_value = 'oc_new'
    state = ('rebuild', 'oc_new', None, 3, 2, 0, [], _dt(2026, 11, 10, tzinfo=_tz.utc),
             None)
    cursor.fetchone.side_effect = [
        ('oc1', 'active', '/tmp/ws'), (3,), state,   # 实现读取序：ctx → target → 读态
    ]
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    monkeypatch.setattr(ar, 'broadcast_revert', lambda sid: None)
    out = ar.undo_from_message('sess_x', 'msg_u2')
    client.revert_stage.assert_not_called()
    client.create_session.assert_called_once()
    client.get_project.assert_called_once()    # 走了 native→stale 促销，非 anchor-missing
    assert out['revert']['mode'] == 'rebuild'
    client.delete_session.assert_not_called()  # 成功路径不触发补偿（换 OC 已提交）


def test_redo_nothing_to_redo(monkeypatch):
    cursor = _MagicMock()
    cursor.fetchone.side_effect = [
        ('oc1', 'active', '/tmp/ws'), None,              # sessions + 无状态行
    ]
    conn = _MagicMock(); conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: conn; conn.__exit__ = lambda self, *a: False

    @_cm
    def fake_db():
        yield conn
    monkeypatch.setattr(ar, 'get_db', fake_db)
    import pytest
    with pytest.raises(ar.UndoError) as ei:
        ar.redo('sess_x')
    assert ei.value.code == 'NOTHING_TO_REDO'


def test_redo_success_clears_and_restores(monkeypatch):
    """重做成功：OC revert_clear + stash 搬回 + 上下文重放旗标清位。"""
    cursor = _MagicMock()
    cursor.fetchone.side_effect = [
        ('oc1', 'active', '/tmp/ws'),                        # sessions 行
        ('native', 'oc1', 'msg_o3', 3, 2, 1, ['a.txt'],
         _dt(2026, 11, 10, tzinfo=_tz.utc), None),           # 读态行（无 prev）
    ]
    cursor.fetchall.side_effect = [[]]                       # _message_window 空窗
    conn = _MagicMock(); conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: conn; conn.__exit__ = lambda self, *a: False

    @_cm
    def fake_db():
        yield conn
    client = _MagicMock()
    restore_calls, replay_calls = [], []
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    monkeypatch.setattr(ar, 'restore_stash', lambda conn, sid: restore_calls.append(sid))
    monkeypatch.setattr(ar, 'clear_context_replay',
                        lambda conn, sid: replay_calls.append(sid))
    monkeypatch.setattr(ar, 'broadcast_revert', lambda sid: None)
    out = ar.redo('sess_x')
    client.revert_clear.assert_called_once_with('oc1', directory='/tmp/ws')
    assert restore_calls == ['sess_x']
    assert replay_calls == ['sess_x']
    assert out['deleted'] == 2
    assert out['messages'] == []


def test_redo_stale_commits_closeout_in_own_tx(monkeypatch):
    """REDO_STALE：收口必须落在独立事务并正常提交——若与 raise 同块，
    get_db 异常路径回滚会让「撤销已收口」落空（C1 回归）。"""
    cursor = _MagicMock()
    cursor.fetchone.side_effect = [
        ('oc_old', 'active', '/tmp/ws'),                     # 当前 OC 已换
        ('native', 'oc1', 'msg_o3', 3, 2, 1, ['a.txt'],
         _dt(2026, 11, 10, tzinfo=_tz.utc), None),           # 状态行 OC=oc1 ≠ oc_old
    ]
    fake_db, txs = _tracking_db(cursor)
    commit_conns = []
    monkeypatch.setattr(ar, 'commit_revert',
                        lambda conn, sid: commit_conns.append(conn))
    monkeypatch.setattr(ar, 'clear_context_replay', lambda conn, sid: None)
    monkeypatch.setattr(ar, 'get_db', fake_db)
    client = _MagicMock()
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    import pytest
    with pytest.raises(ar.UndoError) as ei:
        ar.redo('sess_x')
    assert ei.value.code == 'REDO_STALE'
    assert commit_conns                                      # 收口确已发生
    assert any(t['conn'] is c and t['exc'] is None           # 且在正常提交的事务里
               for t in txs for c in commit_conns), \
        '收口写入被 REDO_STALE 异常一并回滚（C1）'
    client.revert_clear.assert_not_called()                  # 旧 OC 上下文不可达


def test_redo_rebuild_switches_back_to_prev_session(monkeypatch):
    """rebuild 重做（I1 / spec §2.2d）：切回 prev OC 会话 + 清标记 + 删除撤销时
    新建的空会话 + stash 搬回。哨兵用例：prev_opencode_session_id 此前只写不读，
    重做后 sessions.opencode_session_id 必须指回旧会话（探针场景 C 同路径）。"""
    cursor = _MagicMock()
    cursor.fetchone.side_effect = [
        ('oc_new', 'active', '/tmp/ws'),                     # sessions 行（undo 时已切到新空会话）
        ('rebuild', 'oc_new', None, 3, 2, 0, [],
         _dt(2026, 11, 10, tzinfo=_tz.utc), 'oc_old'),       # 状态行（含 prev=oc_old）
    ]
    cursor.fetchall.side_effect = [[]]                       # _message_window 空窗
    fake_db, txs = _tracking_db(cursor)
    client = _MagicMock()
    restore_calls = []
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    monkeypatch.setattr(ar, 'restore_stash',
                        lambda conn, sid: restore_calls.append(sid))
    monkeypatch.setattr(ar, 'clear_context_replay', lambda conn, sid: None)
    monkeypatch.setattr(ar, 'broadcast_revert', lambda sid: None)
    ar.redo('sess_x')
    assert restore_calls == ['sess_x']                       # stash 恢复被调
    # 切回 UPDATE 已发出且参数=旧会话（先搬回 stash、再切回，同一事务）
    update_calls = [c for c in cursor.execute.call_args_list
                    if 'UPDATE ai_chat_sessions' in str(c.args[0])]
    assert len(update_calls) == 1
    assert update_calls[0].args[1] == ('oc_old', 'sess_x')
    # 撤销时新建的空会话被删除（事务提交后 best-effort）
    client.delete_session.assert_called_once_with('oc_new')
    # rebuild 无 OC 撤销点 → 不调 revert_clear
    client.revert_clear.assert_not_called()
    assert all(t['exc'] is None for t in txs)                # 两个事务都正常提交


def test_redo_busy_409(monkeypatch):
    """redo 与 undo 对称的 busy 守卫（I2）：监听器活着 → 409 SESSION_BUSY。"""
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: True)
    import pytest
    with pytest.raises(ar.UndoError) as ei:
        ar.redo('sess_x')
    assert ei.value.code == 'SESSION_BUSY'
    assert ei.value.operation == 'redo'


def test_redo_oc_409_maps_to_session_busy(monkeypatch):
    """OC revert_clear 回 409（回合进行中）→ SESSION_BUSY（redo 侧镜像 undo 的
    映射）；异常抛出使事务回滚＝什么都没改。"""
    cursor = _MagicMock()
    cursor.fetchone.side_effect = [
        ('oc1', 'active', '/tmp/ws'),
        ('native', 'oc1', 'msg_o3', 3, 2, 1, ['a.txt'],
         _dt(2026, 11, 10, tzinfo=_tz.utc), None),
    ]
    fake_db, txs = _tracking_db(cursor)
    import requests as _rq
    boom = _rq.HTTPError('409')
    boom.response = _MagicMock(status_code=409)
    client = _MagicMock()
    client.revert_clear.side_effect = boom
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    monkeypatch.setattr(ar, 'restore_stash', lambda conn, sid: None)
    import pytest
    with pytest.raises(ar.UndoError) as ei:
        ar.redo('sess_x')
    assert ei.value.code == 'SESSION_BUSY'
    assert ei.value.operation == 'redo'
    assert any(t['exc'] is ar.UndoError for t in txs)        # 写事务已回滚


def test_commit_after_dispatch_noop_without_state(monkeypatch):
    calls = []
    monkeypatch.setattr(ar, 'get_revert_state', lambda conn, sid: None)
    monkeypatch.setattr(ar, 'commit_revert', lambda conn, sid: calls.append(sid))

    @_cm
    def fake_db():
        yield _MagicMock()
    monkeypatch.setattr(ar, 'get_db', fake_db)
    ar.commit_after_dispatch('s1')
    assert calls == []


def test_preview_shape(monkeypatch):
    oc = _oc_anchor_msgs(recent_ms=int(_time.time() * 1000))   # 新鲜快照 → full
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc)
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    out = ar.preview_undo('sess_x', 'msg_u2')
    assert out['messageCount'] == 3
    assert out['files'] == ['a.txt']
    assert out['fileCapability'] == 'full'
    assert out['mode'] == 'native'


def test_commit_after_dispatch_clears_state(monkeypatch):
    calls = []
    monkeypatch.setattr(ar, 'get_revert_state',
                        lambda conn, sid: {'mode': 'native'} if sid == 's1' else None)
    monkeypatch.setattr(ar, 'commit_revert',
                        lambda conn, sid: calls.append(('commit', sid)) or 3)
    monkeypatch.setattr(ar, 'clear_context_replay',
                        lambda conn, sid: calls.append(('flag', sid)))
    monkeypatch.setattr(ar, 'broadcast_revert', lambda sid: calls.append(('bc', sid)))

    @_cm
    def fake_db():
        yield _MagicMock()
    monkeypatch.setattr(ar, 'get_db', fake_db)
    ar.commit_after_dispatch('s1')
    assert ('commit', 's1') in calls and ('bc', 's1') in calls
    ar.commit_after_dispatch('s2')                     # 无状态 → no-op
    assert not any(c == ('commit', 's2') for c in calls)
