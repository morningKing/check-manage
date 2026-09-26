"""批任务自动重试（按错误类别）与运行中对账器的测试。

改进语义：
  - 可重试失败（stalled / tool stuck / provider 瞬时错误白名单 / 网络异常）
    自动把子任务重新排队（retry_count+1，不占批次计数）；已开跑过的在原
    OpenCode 会话续跑（continue_prompt）；预算（AI_BATCH_MAX_AUTO_RETRY，
    默认 2）用尽才落 failed。
  - ProviderAuthError / 用户中断 / 上下文超限 / 硬超时上限：一律直接失败。
  - 对账器：running 但不属于任何工作线程的残账行，与 OpenCode 会话对齐——
    404 带准确原因失败、整体不可达跳过、还活着则原地续跑重排。
"""
import os
import sys
import uuid

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def claim_guard():
    """持有测试行的 FOR KEY SHARE 行锁（独立连接、跨提交不释放）。

    测试与正在运行的 dev 后端共享同一个库——后端的认领用 SELECT ... FOR UPDATE
    SKIP LOCKED，会跳过被锁行，因此不会抢走测试重新排队的 pending 数据按它
    自己的代码处理（曾把断言偶发弄脏成 failed）。KEY SHARE 锁与 FOR UPDATE
    冲突（claim 被跳过）但与测试自身的非键 UPDATE 兼容。"""
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
            (uid, f'ar_user_{uid[:8]}', 'x', f'AR User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_batch(db_conn, user_id, n=1):
    bid = str(uuid.uuid4())
    sids = []
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'retry-test', 'p', %s)", (bid, user_id, n))
        for i in range(n):
            sid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                "VALUES (%s, %s, 'pending', %s, %s, %s)",
                (sid, user_id, bid, i, f'f{i}.csv'))
            sids.append(sid)
    db_conn.commit()
    return bid, sids


def _row(db_conn, sid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, retry_count, continue_prompt, error_message, "
                    "opencode_session_id FROM ai_chat_sessions WHERE id=%s", (sid,))
        return cur.fetchone()


def _batch_row(db_conn, bid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, done, failed FROM ai_chat_batches WHERE id=%s", (bid,))
        return cur.fetchone()


def _idle_msg():
    """无文本、无工具的冻结消息 —— STALL 看门狗的触发形状。"""
    return {'role': 'assistant', 'finished': False, 'finish': None,
            'running_tool': False, 'content': [], 'id': 'm1', 'error': None}


def _stall_worker(monkeypatch, fake_extra=None):
    import utils.batch_engine as eng
    worker = eng.BatchWorker()
    worker.SESSION_TIMEOUT_SEC = 0     # 无硬上限，纯靠 STALL 触发
    worker.STALL_TIMEOUT_SEC = 0.15
    worker.POLL_INTERVAL_SEC = 0.02
    worker.QUESTION_CHECK_INTERVAL = 999
    return worker


def _patch_oc(monkeypatch, **methods):
    import utils.batch_engine as eng
    monkeypatch.setattr(eng, 'opencode_client', type('F', (), methods)())


def test_stall_failure_auto_retries_with_continue_then_fails(db_conn, user_id,
                                                              claim_guard,
                                                              monkeypatch, tmp_path):
    import utils.batch_engine as eng
    bid, sids = _seed_batch(db_conn, user_id)
    sid = sids[0]
    claim_guard(sid)
    worker = _stall_worker(monkeypatch)

    def create_session(directory='', title=''):
        return 'oc-stall'
    _patch_oc(monkeypatch,
              create_session=staticmethod(create_session),
              send_message=staticmethod(lambda *a, **k: None),
              list_messages=staticmethod(lambda oc, directory='': [_idle_msg()]))
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))

    worker._run_one({'id': sid, 'user_id': user_id, 'batch_id': bid,
                     'batch_input_file': 'x.csv', 'input_files': None,
                     'scan_task_id': None, 'opencode_session_id': None,
                     'workspace_path': str(tmp_path), 'continue_prompt': None,
                     'agent': '', 'model': '', 'fencing_token': 0})

    status, retry_count, cp, err, oc = _row(db_conn, sid)
    assert status == 'pending'                    # 重新排队而非 failed
    assert retry_count == 1
    assert oc == 'oc-stall'                       # 原会话续跑
    assert cp and '继续' in cp
    status_b, done, failed = _batch_row(db_conn, bid)
    assert (done, failed) == (0, 0)               # 重试不占批次计数

    # 预算用尽：同样失败这次落 failed
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='running', retry_count=2 "
                    "WHERE id=%s RETURNING execution_generation", (sid,))
        _gen = cur.fetchone()[0]
    db_conn.commit()
    worker._run_one({'id': sid, 'user_id': user_id, 'batch_id': bid,
                     'batch_input_file': 'x.csv', 'input_files': None,
                     'scan_task_id': None, 'opencode_session_id': 'oc-stall',
                     'workspace_path': str(tmp_path), 'continue_prompt': None,
                     'agent': '', 'model': '', 'execution_generation': _gen,
                     'fencing_token': 0})
    status, retry_count, cp, err, oc = _row(db_conn, sid)
    assert status == 'failed'
    assert '没有任何新进展' in err


def test_provider_auth_never_retries(db_conn, user_id, claim_guard,
                                     monkeypatch, tmp_path):
    import utils.batch_engine as eng
    bid, sids = _seed_batch(db_conn, user_id)
    sid = sids[0]
    claim_guard(sid)
    worker = _stall_worker(monkeypatch)
    auth_err = [{'role': 'assistant', 'finished': True, 'finish': 'stop',
                 'running_tool': False,
                 'content': [{'type': 'text', 'text': ''}], 'id': 'm1',
                 'error': {'name': 'ProviderAuthError',
                           'data': {'message': 'invalid api key'}}}]
    _patch_oc(monkeypatch,
              create_session=staticmethod(lambda directory='', title='': 'oc-auth'),
              send_message=staticmethod(lambda *a, **k: None),
              list_messages=staticmethod(lambda oc, directory='': auth_err))
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))
    # P0 CAS：终态写回要求 status='running'（真实 claim 后的状态）
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='running' WHERE id=%s", (sid,))
    db_conn.commit()

    worker._run_one({'id': sid, 'user_id': user_id, 'batch_id': bid,
                     'batch_input_file': 'x.csv', 'input_files': None,
                     'scan_task_id': None, 'opencode_session_id': None,
                     'workspace_path': str(tmp_path), 'continue_prompt': None,
                     'agent': '', 'model': '', 'execution_generation': 0,
                     'fencing_token': 0})

    status, retry_count, cp, err, oc = _row(db_conn, sid)
    assert status == 'failed'                     # 密钥错：重试必然再炸
    assert retry_count == 0
    assert 'ProviderAuthError' in err


def test_network_exception_requeues_fresh(db_conn, user_id, monkeypatch, tmp_path):
    """create_session 网络异常 → 重排队全新执行（无 oc 会话 → 不置续跑词）。"""
    import utils.batch_engine as eng
    bid, sids = _seed_batch(db_conn, user_id)
    sid = sids[0]
    worker = _stall_worker(monkeypatch)

    def _refused(*a, **k):
        raise requests.exceptions.ConnectionError('connection refused')

    _patch_oc(monkeypatch, create_session=staticmethod(_refused))
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))

    worker._run_one({'id': sid, 'user_id': user_id, 'batch_id': bid,
                     'batch_input_file': 'x.csv', 'input_files': None,
                     'scan_task_id': None, 'opencode_session_id': None,
                     'workspace_path': str(tmp_path), 'continue_prompt': None,
                     'agent': '', 'model': '', 'fencing_token': 0})

    status, retry_count, cp, err, oc = _row(db_conn, sid)
    assert status == 'pending'
    assert retry_count == 1
    assert cp is None                             # 从未开跑：全新执行
    assert oc is None


# ---------------------------------------------------------------------------
# 运行中对账器
# ---------------------------------------------------------------------------

def _seed_running(db_conn, user_id, claim_guard, oc='oc-lost', ws='/tmp/whatever'):
    bid, sids = _seed_batch(db_conn, user_id, n=1)
    sid = sids[0]
    claim_guard(sid)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='running', "
                    "opencode_session_id=%s, workspace_path=%s WHERE id=%s",
                    (oc, ws, sid))
    db_conn.commit()
    return bid, sid


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.exceptions.HTTPError(f'{status}', response=resp)


def test_reconcile_404_marks_failed_with_accurate_reason(db_conn, user_id,
                                                          claim_guard, monkeypatch):
    import utils.batch_engine as eng
    bid, sid = _seed_running(db_conn, user_id, claim_guard, oc='oc-gone')
    worker = eng.BatchWorker()
    _patch_oc(monkeypatch, get_messages=staticmethod(
        lambda oc, directory='': (_ for _ in ()).throw(_http_error(404))))

    worker._reconcile_stale_running()

    status, retry_count, cp, err, oc = _row(db_conn, sid)
    assert status == 'failed'
    assert 'OpenCode 会话已失效' in err            # 准确原因，不是"无进展"


def test_reconcile_alive_session_requeues(db_conn, user_id, claim_guard, monkeypatch):
    import utils.batch_engine as eng
    bid, sid = _seed_running(db_conn, user_id, claim_guard, oc='oc-alive')
    worker = eng.BatchWorker()
    _patch_oc(monkeypatch, get_messages=staticmethod(lambda oc, directory='': []))

    worker._reconcile_stale_running()

    status, retry_count, cp, err, oc = _row(db_conn, sid)
    assert status == 'pending'                    # 活着但没人轮询 → 重新排队续跑
    assert retry_count == 1
    assert cp and '继续' in cp


def test_reconcile_outage_skips_rows(db_conn, user_id, claim_guard, monkeypatch):
    """OpenCode 整体不可达：不能把所有 running 行批量误杀。"""
    import utils.batch_engine as eng
    bid, sid = _seed_running(db_conn, user_id, claim_guard, oc='oc-any')
    worker = eng.BatchWorker()

    def _refused(oc, directory=''):
        raise requests.exceptions.ConnectionError('refused')

    _patch_oc(monkeypatch, get_messages=staticmethod(_refused))
    worker._reconcile_stale_running()
    assert _row(db_conn, sid)[0] == 'running'     # 原样保留


def test_reconcile_skips_tracked_rows(db_conn, user_id, claim_guard, monkeypatch):
    """本进程正在跑的行（已入账）不归对账器管——轮询循环自己负责。"""
    import utils.batch_engine as eng
    bid, sid = _seed_running(db_conn, user_id, claim_guard, oc='oc-live')
    worker = eng.BatchWorker()
    worker._running_session_ids.add(sid)          # 模拟正在跑
    _patch_oc(monkeypatch, get_messages=staticmethod(lambda oc, directory='': []))

    worker._reconcile_stale_running()
    assert _row(db_conn, sid)[0] == 'running'


def test_reconcile_budget_exhausted_fails(db_conn, user_id, claim_guard, monkeypatch):
    import utils.batch_engine as eng
    bid, sid = _seed_running(db_conn, user_id, claim_guard, oc='oc-alive')
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET retry_count=2 WHERE id=%s", (sid,))
    db_conn.commit()
    worker = eng.BatchWorker()
    _patch_oc(monkeypatch, get_messages=staticmethod(lambda oc, directory='': []))

    worker._reconcile_stale_running()
    status, retry_count, cp, err, oc = _row(db_conn, sid)
    assert status == 'failed'
    assert '预算已用尽' in err
