"""停滞/工具卡死失败的诊断现场（child.stall 事件 + error_message 尾巴）。

背景：180s STALL 失败只有一句用户文案，判定现场（当时最后一条消息的
finish/running_tool/工具 part 状态、pending question/permission 数）全部丢失，
事后无法区分「provider 真死」vs「平台轮询看不见」vs「ask 挂起没被自动拒绝」。
本套断言：
  1. _await_finished 触发 STALL/tool-stuck 时，异常携带结构化 diagnosis
     （最后一条 assistant 消息关键字段 + 最近工具 part + pending ask 计数）；
  2. 落 failed 时 error_message 尾部追加单行【诊断】后缀；
  3. 每次尝试都落一条 child.stall 批事件（action=failed / auto_retry）——
     error_message 只剩最后一次，事件是逐次重试的唯一持久证据。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'sd_user_{uid[:8]}', 'x', f'SD User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_batch_events WHERE batch_id IN "
                    "(SELECT id FROM ai_chat_batches WHERE user_id = %s)", (uid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_batch(db_conn, user_id):
    bid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'stall-diag', 'p', 1)", (bid, user_id))
        sid = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
            "VALUES (%s, %s, 'pending', %s, 0, 'f0.csv')", (sid, user_id, bid))
    db_conn.commit()
    return bid, sid


def _diag_worker(monkeypatch, list_messages, list_questions=None,
                 list_permissions=None):
    import utils.batch_engine as eng
    worker = eng.BatchWorker()
    worker.SESSION_TIMEOUT_SEC = 0      # 无硬上限，纯靠 STALL / tool-stuck 触发
    worker.POLL_INTERVAL_SEC = 0.02
    worker.QUESTION_CHECK_INTERVAL = 999  # 关掉周期性自动拒绝，隔离看门狗本身
    methods = {
        'create_session': staticmethod(lambda directory='', title='': 'oc-diag'),
        'send_message': staticmethod(lambda *a, **k: None),
        'list_messages': staticmethod(
            lambda oc, directory='': list_messages()),
    }
    if list_questions is not None:
        methods['list_questions'] = staticmethod(
            lambda directory='': list_questions())
    if list_permissions is not None:
        methods['list_permissions'] = staticmethod(
            lambda directory='': list_permissions())
    monkeypatch.setattr(eng, 'opencode_client',
                        type('F', (), methods)())
    return worker, eng


def test_stall_diagnosis_carries_last_message_and_pending_ask(monkeypatch):
    """STALL 触发瞬间：诊断必须回答「当时有没有工具在跑 / 有没有 pending
    ask / 最后一条消息长什么样」。构造形状 = 有 pending question 工具 part
    但 running_tool=False（判定矛盾型现场，正是事后最难还原的那种）。"""
    from utils.batch_engine import _SessionTimeout

    frozen = {'role': 'assistant', 'finished': False, 'finish': None,
              'running_tool': False, 'id': 'm-late', 'error': None,
              'content': [{'type': 'text', 'text': 'partial'},
                          {'type': 'tool_use', 'name': 'question',
                           'status': 'pending', 'output_len': 0}]}
    worker, eng = _diag_worker(
        monkeypatch, lambda: [frozen],
        list_questions=lambda: [{'id': 'q1', 'sessionID': 'oc-diag'}],
        list_permissions=lambda: [])
    worker.STALL_TIMEOUT_SEC = 0.15

    with pytest.raises(_SessionTimeout) as ei:
        worker._await_finished('oc-diag', 'sid-test')

    d = ei.value.diagnosis
    assert d['lastMsgId'] == 'm-late'
    assert d['finish'] is None
    assert d['runningTool'] is False
    assert d['toolParts'] == [{'name': 'question', 'status': 'pending'}]
    assert d['pendingQuestions'] == 1
    assert d['pendingPermissions'] == 0
    assert d['msgCount'] == 1
    assert d['sig']            # 最后一次进度签名（有消息就有非空签名）


def test_tool_stuck_diagnosis_records_running_tool(monkeypatch):
    """工具卡死（900s 看门狗）触发时：runningTool=True + 工具名/状态入诊断，
    事后才能回答「是哪个工具卡住、当时是否有 pending ask」。"""
    from utils.batch_engine import _SessionTimeout

    hung = {'role': 'assistant', 'finished': False, 'finish': 'tool-calls',
            'running_tool': True, 'id': 'm-hang', 'error': None,
            'content': [{'type': 'tool_use', 'name': 'bash',
                         'status': 'running', 'output_len': 12}]}
    worker, eng = _diag_worker(
        monkeypatch, lambda: [hung],
        list_questions=lambda: [],
        list_permissions=lambda: [{'id': 'p1', 'sessionID': 'oc-diag'}])
    worker.STALL_TIMEOUT_SEC = 999
    worker.TOOL_STALL_TIMEOUT_SEC = 0.15

    with pytest.raises(_SessionTimeout) as ei:
        worker._await_finished('oc-diag', 'sid-test')

    assert ei.value.reason == 'tool stuck'
    d = ei.value.diagnosis
    assert d['runningTool'] is True
    assert d['toolParts'] == [{'name': 'bash', 'status': 'running'}]
    assert d['pendingPermissions'] == 1
    assert d['pendingQuestions'] == 0


def test_stall_verdict_aborts_opencode_turn(monkeypatch):
    """stalled 判死必须 abort 原回合：不 abort 的话原回合在 OpenCode 里
    继续跑、继续输出甚至完成——与任务行 failed 状态分裂（生产实测
    「报 181 秒无进展但会话还在继续输出」，2026-11-03）。tool-stuck 路径
    早有同款 abort（先停掉别空转烧 token），stall 路径对齐。"""
    frozen = {'role': 'assistant', 'finished': False, 'finish': None,
              'running_tool': False, 'id': 'm1', 'error': None, 'content': []}
    worker, eng = _diag_worker(
        monkeypatch, lambda: [frozen],
        list_questions=lambda: [], list_permissions=lambda: [])
    worker.STALL_TIMEOUT_SEC = 0.15
    # content=[] 是思考盲区形态（走 THINKING 阈值），测试意图是判死后的
    # abort 行为链路——两层阈值一起压小，快速走到判死。
    worker.THINKING_STALL_TIMEOUT_SEC = 0.15
    aborted = []
    monkeypatch.setattr(eng.opencode_client, 'abort_session',
                        staticmethod(lambda oc, directory='': aborted.append(oc)),
                        raising=False)

    with pytest.raises(eng._SessionTimeout) as ei:
        worker._await_finished('oc-diag', 'sid-test')

    assert ei.value.reason == 'stalled (no progress)'
    assert aborted == ['oc-diag']            # 判死即停，不留分裂状态


_ROW_FIELDS = ('id', 'user_id', 'batch_id', 'batch_input_file', 'input_files',
               'scan_task_id', 'opencode_session_id', 'workspace_path',
               'continue_prompt', 'agent', 'model', 'fencing_token')


def _run_one_row(sid, user_id, bid, tmp_path, oc=None):
    return {'id': sid, 'user_id': user_id, 'batch_id': bid,
            'batch_input_file': 'x.csv', 'input_files': None,
            'scan_task_id': None, 'opencode_session_id': oc,
            'workspace_path': str(tmp_path), 'continue_prompt': None,
            'agent': '', 'model': '', 'fencing_token': 0}


def test_run_one_stall_failed_appends_event_and_error_tail(
        db_conn, user_id, monkeypatch, tmp_path):
    """预算用尽落 failed：error_message 尾部带【诊断】单行 + 落一条
    child.stall 事件（action=failed，payload 含结构化 diagnosis）。"""
    import utils.batch_engine as eng
    bid, sid = _seed_batch(db_conn, user_id)
    frozen = {'role': 'assistant', 'finished': False, 'finish': None,
              'running_tool': False, 'id': 'm1', 'error': None, 'content': []}
    worker, eng = _diag_worker(
        monkeypatch, lambda: [frozen],
        list_questions=lambda: [{'id': 'q1', 'sessionID': 'oc-diag'}],
        list_permissions=lambda: [])
    worker.STALL_TIMEOUT_SEC = 0.15
    worker.THINKING_STALL_TIMEOUT_SEC = 0.15   # 盲区形态两层阈值一起压小
    monkeypatch.setattr(eng, '_prepare_workspace',
                        lambda *a, **kw: str(tmp_path))
    with db_conn.cursor() as cur:
        # 预算用尽：retry_count 已到 MAX_AUTO_RETRY，这次直接落 failed
        cur.execute("UPDATE ai_chat_sessions SET status='running', "
                    "retry_count=2 WHERE id=%s", (sid,))
    db_conn.commit()

    worker._run_one(_run_one_row(sid, user_id, bid, tmp_path))

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, error_message FROM ai_chat_sessions "
                    "WHERE id=%s", (sid,))
        status, err = cur.fetchone()
        cur.execute("SELECT payload FROM ai_batch_events "
                    "WHERE batch_id=%s AND event_type='child.stall' "
                    "ORDER BY event_seq", (bid,))
        events = [r[0] for r in cur.fetchall()]
    assert status == 'failed'
    assert '没有任何新进展' in err
    assert '【诊断】' in err
    assert 'pendingQ=1' in err
    assert len(events) == 1
    ev = events[0]
    assert ev['action'] == 'failed'
    assert ev['reason'] == 'stalled (no progress)'
    assert ev['diagnosis']['pendingQuestions'] == 1
    assert ev['session_id'] == sid


def test_run_one_stall_auto_retry_appends_event_without_error_tail(
        db_conn, user_id, monkeypatch, tmp_path):
    """预算内失败走自动重试：事件 action=auto_retry（每次尝试一条持久证据），
    会话行 error_message 保持空（还没终局，不该带诊断尾巴吓人）。"""
    import utils.batch_engine as eng
    bid, sid = _seed_batch(db_conn, user_id)
    frozen = {'role': 'assistant', 'finished': False, 'finish': None,
              'running_tool': False, 'id': 'm1', 'error': None, 'content': []}
    worker, eng = _diag_worker(
        monkeypatch, lambda: [frozen],
        list_questions=lambda: [], list_permissions=lambda: [])
    worker.STALL_TIMEOUT_SEC = 0.15
    worker.THINKING_STALL_TIMEOUT_SEC = 0.15   # 盲区形态两层阈值一起压小
    monkeypatch.setattr(eng, '_prepare_workspace',
                        lambda *a, **kw: str(tmp_path))
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='running', "
                    "opencode_session_id='oc-diag' WHERE id=%s", (sid,))
    db_conn.commit()

    worker._run_one(_run_one_row(sid, user_id, bid, tmp_path, oc='oc-diag'))

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, retry_count, error_message "
                    "FROM ai_chat_sessions WHERE id=%s", (sid,))
        status, retry_count, err = cur.fetchone()
        cur.execute("SELECT payload FROM ai_batch_events "
                    "WHERE batch_id=%s AND event_type='child.stall' "
                    "ORDER BY event_seq", (bid,))
        events = [r[0] for r in cur.fetchall()]
    assert status == 'pending'                    # 重新排队而非 failed
    assert retry_count == 1
    assert not err                                # 未终局：不带诊断尾巴
    assert len(events) == 1
    assert events[0]['action'] == 'auto_retry'
    assert events[0]['reason'] == 'stalled (no progress)'
