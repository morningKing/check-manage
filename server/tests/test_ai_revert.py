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
