"""批任务「暂停/继续/中断」与无人值守防卡死的测试。

覆盖：
  - pause_batch：pause_requested 置位 + 批次立即 'paused'
  - worker 预检 sweep：pending + pause_requested → 'paused'（不占 failed 计数）
  - resume_batch：paused/cancelled 都能恢复；计数回滚只针对 cancelled
  - cancel_batch：已暂停的子任务直接落成 cancelled（worker 扫不到它们）
  - question 自动拒绝：只拒绝本会话的 pending 问题
  - 工具卡死看门狗：普通工具冻结超时 → abort + 'tool stuck'；task 工具背后
    的子代理仍在产出 → 顺延不杀
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def claim_guard():
    """持有测试行的 FOR KEY SHARE 行锁（独立连接、跨提交不释放），防止共享库
    上正在运行的 dev 后端用 SKIP LOCKED 认领抢走 pending 的测试数据。KEY SHARE
    与 FOR UPDATE 冲突（claim 被跳过）但与测试自身的非键 UPDATE 兼容。"""
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    try:
        def hold(*sids):
            if not sids:
                return
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM ai_chat_sessions "
                            "WHERE id = ANY(%s) FOR KEY SHARE", (list(sids),))
        yield hold
    finally:
        conn.rollback()
        conn.close()



@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'ps_user_{uid[:8]}', 'x', f'PS User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_batch(db_conn, user_id, claim_guard=None, n=2):
    bid = str(uuid.uuid4())
    sids = []
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'pause-test', 'p', %s)",
            (bid, user_id, n),
        )
        for i in range(n):
            sid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                "VALUES (%s, %s, 'pending', %s, %s, %s)",
                (sid, user_id, bid, i, f'f{i}.csv'),
            )
            sids.append(sid)
    db_conn.commit()
    if claim_guard is not None:
        claim_guard(*sids)
    return bid, sids


def _rows(db_conn, sids):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id, status, continue_prompt, error_message "
            "FROM ai_chat_sessions WHERE id = ANY(%s) ORDER BY batch_seq", (sids,))
        return {r[0]: r[1:] for r in cur.fetchall()}


def _batch(db_conn, bid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, done, failed, total FROM ai_chat_batches WHERE id=%s", (bid,))
        return cur.fetchone()


# ---------------------------------------------------------------------------
# 暂停 / 继续 / 中断
# ---------------------------------------------------------------------------

def test_pause_batch_flags_children_and_marks_batch(db_conn, user_id, claim_guard):
    from utils.batch_repo import pause_batch
    bid, sids = _seed_batch(db_conn, user_id, claim_guard, n=2)
    result = pause_batch(user_id, bid)
    assert result['batch']['status'] == 'paused'   # 即时反馈
    with db_conn.cursor() as cur:
        cur.execute("SELECT pause_requested, status FROM ai_chat_sessions "
                    "WHERE id = ANY(%s) ORDER BY batch_seq", (sids,))
        rows = cur.fetchall()
    assert all(r[0] is True and r[1] == 'pending' for r in rows)  # 等 worker 落 paused


def test_worker_sweep_lands_paused_without_failed_count(db_conn, user_id, claim_guard):
    from utils.batch_repo import pause_batch
    from utils.batch_engine import get_worker
    bid, sids = _seed_batch(db_conn, user_id, claim_guard, n=2)
    pause_batch(user_id, bid)
    worker = get_worker()
    worker._cancel_pending_requests()
    rows = _rows(db_conn, sids)
    assert all(v[0] == 'paused' for v in rows.values())
    status, done, failed, total = _batch(db_conn, bid)
    assert status == 'paused'
    assert (done, failed, total) == (0, 0, 2)      # 暂停不占 failed


def test_resume_batch_resumes_paused_without_counter_change(db_conn, user_id, claim_guard):
    from utils.batch_repo import pause_batch, resume_batch
    from utils.batch_engine import get_worker
    bid, sids = _seed_batch(db_conn, user_id, claim_guard, n=2)
    # 一个暂停前已开跑（有 oc 会话 → resume 应置 continue_prompt）
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET opencode_session_id='oc_1' "
                    "WHERE id=%s", (sids[0],))
    db_conn.commit()
    pause_batch(user_id, bid)
    get_worker()._cancel_pending_requests()        # 落 paused
    result = resume_batch(user_id, bid)
    # worker 尚未认领，全部子任务待运行 → 批次 'pending'（部分完成时才是 running）
    assert result['batch']['status'] == 'pending'
    rows = _rows(db_conn, sids)
    assert all(v[0] == 'pending' for v in rows.values())
    assert rows[sids[0]][1] and '继续' in rows[sids[0]][1]
    assert rows[sids[1]][1] is None
    status, done, failed, total = _batch(db_conn, bid)
    assert (status, done, failed, total) == ('pending', 0, 0, 2)


def test_cancel_batch_converts_paused_children(db_conn, user_id, claim_guard):
    from utils.batch_repo import pause_batch, cancel_batch
    from utils.batch_engine import get_worker
    bid, sids = _seed_batch(db_conn, user_id, claim_guard, n=2)
    pause_batch(user_id, bid)
    get_worker()._cancel_pending_requests()        # → paused
    result = cancel_batch(user_id, bid)
    rows = _rows(db_conn, sids)
    assert all(v[0] == 'cancelled' for v in rows.values())
    status, done, failed, total = _batch(db_conn, bid)
    assert status == 'failed'                      # 全部取消 → failed
    assert failed == 2


# ---------------------------------------------------------------------------
# question 自动拒绝
# ---------------------------------------------------------------------------

def test_auto_reject_only_own_session_questions(monkeypatch):
    import utils.batch_engine as eng
    rejected = []
    fake = type('F', (), {
        'list_questions': staticmethod(lambda directory='': [
            {'id': 'q1', 'sessionID': 'oc'},     # 本会话 → 拒
            {'id': 'q2', 'sessionID': 'other'},  # 别的会话 → 不动
            {'id': None, 'sessionID': 'oc'},     # 畸形 → 跳过
        ]),
        'reject_question': staticmethod(lambda qid, directory='': rejected.append(qid)),
    })()
    monkeypatch.setattr(eng, 'opencode_client', fake)
    eng.BatchWorker()._reject_pending_questions('oc', directory='/ws')
    assert rejected == ['q1']


def test_auto_reject_swallows_opencode_errors(monkeypatch):
    import utils.batch_engine as eng

    def _boom(directory=''):
        raise RuntimeError('opencode down')

    fake = type('F', (), {'list_questions': staticmethod(_boom)})
    monkeypatch.setattr(eng, 'opencode_client', fake)
    # 不抛异常即可（辅助检查绝不能打断主轮询）
    eng.BatchWorker()._reject_pending_questions('oc')


# ---------------------------------------------------------------------------
# 工具卡死看门狗
# ---------------------------------------------------------------------------

def _msg(role='assistant', finish=None, running_tool=False, content=None,
         mid='m1', error=None):
    return {'role': role, 'finished': bool(finish and finish not in ('tool-calls', 'tool_use')),
            'finish': finish, 'running_tool': running_tool,
            'content': content or [], 'id': mid, 'error': error}


def test_tool_stall_watchdog_kills_frozen_plain_tool(monkeypatch):
    """普通工具（非 task）签名冻结超过 TOOL_STALL → abort + 'tool stuck'。"""
    import utils.batch_engine as eng
    from utils.batch_engine import _SessionTimeout
    w = eng.BatchWorker()
    w.SESSION_TIMEOUT_SEC = 0
    w.STALL_TIMEOUT_SEC = 0.2
    w.TOOL_STALL_TIMEOUT_SEC = 0.3
    w.POLL_INTERVAL_SEC = 0.02
    w.QUESTION_CHECK_INTERVAL = 999
    frozen = [_msg(finish='tool-calls', running_tool=True,
                   content=[{'type': 'tool_use', 'name': 'bash', 'status': 'running',
                             'output_len': 0, 'child_sid': None}])]
    aborted = []
    fake = type('F', (), {
        'list_messages': staticmethod(lambda oc, directory='': frozen),
        'get_messages': staticmethod(lambda oc, directory='': []),
        'abort_session': staticmethod(lambda oc, directory='': aborted.append(oc)),
    })()
    monkeypatch.setattr(eng, 'opencode_client', fake)
    with pytest.raises(_SessionTimeout) as ei:
        w._await_finished('oc', 'sid-test')
    assert ei.value.reason == 'tool stuck'
    assert '工具调用' in str(ei.value)
    assert aborted == ['oc']      # 失败前先 abort，别让 OpenCode 空转


def test_tool_stall_deferred_while_subagent_progressing(monkeypatch):
    """task 工具冻结，但子代理自己的消息还在增长 → 看门狗顺延，不误杀，
    直到子代理返回、回合正常完成。"""
    import utils.batch_engine as eng
    w = eng.BatchWorker()
    w.SESSION_TIMEOUT_SEC = 0
    w.STALL_TIMEOUT_SEC = 0.1
    w.TOOL_STALL_TIMEOUT_SEC = 0.15
    w.POLL_INTERVAL_SEC = 0.02
    w.QUESTION_CHECK_INTERVAL = 999
    task_tool = [{'type': 'tool_use', 'name': 'task', 'status': 'running',
                  'output_len': 0, 'child_sid': 'child1'}]
    frozen = [_msg(finish='tool-calls', running_tool=True, content=task_tool)]
    done = [_msg(finish='stop', content=[{'type': 'text', 'text': 'done'}])]
    polls = {'n': 0}
    child_msgs = {'n': 0}

    def fake_list_messages(oc, directory=''):
        # 前 ~40 次轮询（约 0.8s，远超 TOOL_STALL 0.15s）父级一直冻结，
        # 之后子代理返回、回合完成。
        polls['n'] += 1
        return frozen if polls['n'] <= 40 else done

    def fake_get_messages(oc, directory=''):
        # 子代理消息持续增长（每次调用多一条）→ 每个窗口都能看到新进展
        child_msgs['n'] += 1
        return [{'info': {'role': 'assistant', 'id': f'c{i}', 'parts': []}}
                for i in range(child_msgs['n'])]

    fake = type('F', (), {
        'list_messages': staticmethod(fake_list_messages),
        'get_messages': staticmethod(fake_get_messages),
        'abort_session': staticmethod(lambda oc, directory='': (_ for _ in ()).throw(AssertionError('should not abort'))),
    })()
    monkeypatch.setattr(eng, 'opencode_client', fake)
    preview, msg = w._await_finished('oc', 'sid-test')
    assert msg['content'][0]['text'] == 'done'


def test_tool_stall_kills_dead_subagent(monkeypatch):
    """task 工具冻结且子代理消息不再变化 → 看门狗最终触发。"""
    import utils.batch_engine as eng
    from utils.batch_engine import _SessionTimeout
    w = eng.BatchWorker()
    w.SESSION_TIMEOUT_SEC = 0
    w.STALL_TIMEOUT_SEC = 0.1
    w.TOOL_STALL_TIMEOUT_SEC = 0.2
    w.POLL_INTERVAL_SEC = 0.02
    w.QUESTION_CHECK_INTERVAL = 999
    task_tool = [{'type': 'tool_use', 'name': 'task', 'status': 'running',
                  'output_len': 0, 'child_sid': 'child1'}]
    frozen = [_msg(finish='tool-calls', running_tool=True, content=task_tool)]
    fake = type('F', (), {
        'list_messages': staticmethod(lambda oc, directory='': frozen),
        'get_messages': staticmethod(lambda oc, directory='':
                                     [{'info': {'role': 'assistant', 'id': 'c0', 'parts': []}}]),
        'abort_session': staticmethod(lambda oc, directory='': None),
    })()
    monkeypatch.setattr(eng, 'opencode_client', fake)
    with pytest.raises(_SessionTimeout) as ei:
        w._await_finished('oc', 'sid-test')
    assert ei.value.reason == 'tool stuck'


# ---------------------------------------------------------------------------
# 无人值守指令
# ---------------------------------------------------------------------------

def test_batch_directive_prepended_for_fresh_children(user_id, db_conn, claim_guard,
                                                       monkeypatch, tmp_path):
    """非 continue 的批任务子任务：进发给模型的 prompt 必须带无人值守指令
    （禁提问/禁放弃），且记忆存档记的是不含指令的原文。"""
    import utils.batch_engine as eng
    bid, sids = _seed_batch(db_conn, user_id, claim_guard, n=1)
    sid = sids[0]
    captured = {}

    def fake_send(oc, prompt, directory='', agent='', model=''):
        captured['prompt'] = prompt

    monkeypatch.setattr(eng, 'opencode_client',
                        type('F', (), {
                            'create_session': staticmethod(lambda directory, title='': 'oc_x'),
                            'send_message': staticmethod(fake_send),
                            'list_messages': staticmethod(
                                lambda oc, directory='': [_msg(finish='stop',
                                                               content=[{'type': 'text', 'text': 'ok'}])]),
                            'get_messages': staticmethod(lambda oc, directory='': []),
                            'list_agents': staticmethod(lambda directory='': []),
                        })())
    worker = eng.BatchWorker()
    worker.POLL_INTERVAL_SEC = 0
    # 跳过真实工作区准备（staging 文件不存在会走 mark_failed 早退）
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))
    worker._run_one({'id': sid, 'user_id': user_id, 'batch_id': bid,
                     'batch_input_file': 'x.csv', 'input_files': None,
                     'scan_task_id': None, 'opencode_session_id': None,
                     'workspace_path': str(tmp_path), 'continue_prompt': None,
                     'agent': '', 'model': '', 'fencing_token': 0})
    assert captured['prompt'].startswith('[系统规则]')
    assert '禁止使用 question' in captured['prompt']
