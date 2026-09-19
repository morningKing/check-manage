"""Standalone /v1/ai-sessions 子会话的 prompt 保全回归（D4 修复）。

历史缺陷：_run_one 的 standalone 分支在认领时立即清空 continue_prompt；
若 create_session 阶段（OpenCode 不可达）抛网络异常触发自动重排，重排队时
continue_prompt 已被清成 NULL，重跑时 prompt='' —— 子任务只带样板指令空跑。
修复后：standalone 行保留 continue_prompt，任何重新认领都能取回原始请求。
"""
import os
import sys
import uuid

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PROMPT = '帮我总结这份合同的违约条款'


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'sa_user_{uid[:8]}', 'x', f'SA User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_messages WHERE session_id IN "
                    "(SELECT id FROM ai_chat_sessions WHERE user_id = %s)", (uid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


@pytest.fixture
def claim_guard():
    """与 dev 后端共享库：持有 FOR KEY SHARE 行锁防止 worker 抢走测试行。"""
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


def _seed_standalone(db_conn, user_id, continue_prompt=PROMPT):
    sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, continue_prompt) "
            "VALUES (%s, %s, 'pending', %s)", (sid, user_id, continue_prompt))
    db_conn.commit()
    return sid


def _row(db_conn, sid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, retry_count, continue_prompt, error_message, "
                    "opencode_session_id FROM ai_chat_sessions WHERE id=%s", (sid,))
        return cur.fetchone()


def _worker(monkeypatch):
    """隔离长期记忆层（mem0 未配置/外部依赖与本测试无关）后构造 worker。"""
    import utils.batch_engine as eng
    worker = eng.BatchWorker()
    worker.SESSION_TIMEOUT_SEC = 0
    worker.STALL_TIMEOUT_SEC = 60
    worker.POLL_INTERVAL_SEC = 0.02
    worker.QUESTION_CHECK_INTERVAL = 999
    return worker


@pytest.fixture(autouse=True)
def _no_memory(monkeypatch):
    import utils.memory as mem
    monkeypatch.setattr(mem, 'search_memory', lambda *a, **k: [])
    monkeypatch.setattr(mem, 'render_memory_block', lambda hits: '')
    monkeypatch.setattr(mem, 'add_memory', lambda *a, **k: None)


def _patch_oc(monkeypatch, **methods):
    import utils.batch_engine as eng
    monkeypatch.setattr(eng, 'opencode_client', type('F', (), methods)())


_DONE_MSG = {'role': 'assistant', 'finished': True, 'finish': 'stop',
             'running_tool': False,
             'content': [{'type': 'text', 'text': 'done'}], 'id': 'm1',
             'error': None}


def test_early_failure_keeps_original_prompt_for_retry(db_conn, user_id,
                                                       claim_guard, monkeypatch,
                                                       tmp_path):
    """create_session 网络异常 → 重排队后 continue_prompt 仍保留原始请求。"""
    import utils.batch_engine as eng
    sid = _seed_standalone(db_conn, user_id)
    claim_guard(sid)
    worker = _worker(monkeypatch)

    def _refused(*a, **k):
        raise requests.exceptions.ConnectionError('connection refused')

    _patch_oc(monkeypatch, create_session=staticmethod(_refused))
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))

    worker._run_one({'id': sid, 'user_id': user_id, 'batch_id': None,
                     'batch_input_file': None, 'input_files': None,
                     'scan_task_id': None, 'opencode_session_id': None,
                     'workspace_path': str(tmp_path),
                     'continue_prompt': PROMPT, 'agent': '', 'model': ''})

    status, retry_count, cp, err, oc = _row(db_conn, sid)
    assert status == 'pending'                    # 自动重排
    assert retry_count == 1
    assert cp == PROMPT                           # 修复点：原始 prompt 未丢
    assert oc is None


def test_requeued_standalone_reruns_with_original_prompt(db_conn, user_id,
                                                         claim_guard, monkeypatch,
                                                         tmp_path):
    """重排后的重新认领：原始 prompt 被再次发给模型（而非空 prompt 空跑）。"""
    import utils.batch_engine as eng
    sid = _seed_standalone(db_conn, user_id)
    claim_guard(sid)
    worker = _worker(monkeypatch)
    sent = []

    def create_session(directory='', title=''):
        return 'oc-recovered'

    def send_message(oc, prompt, directory='', agent=None, model=None, **k):
        sent.append(prompt)

    _patch_oc(monkeypatch,
              create_session=staticmethod(create_session),
              send_message=staticmethod(send_message),
              list_messages=staticmethod(lambda oc, directory='': [_DONE_MSG]))
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))

    worker._run_one({'id': sid, 'user_id': user_id, 'batch_id': None,
                     'batch_input_file': None, 'input_files': None,
                     'scan_task_id': None, 'opencode_session_id': None,
                     'workspace_path': str(tmp_path),
                     'continue_prompt': PROMPT, 'agent': '', 'model': ''})

    status, retry_count, cp, err, oc = _row(db_conn, sid)
    assert status == 'completed'
    assert sent, 'prompt should have been dispatched'
    assert PROMPT in sent[0]                      # 原始请求在 prompt 里


def test_fresh_standalone_claim_does_not_use_continue_mode(db_conn, user_id,
                                                           claim_guard,
                                                           monkeypatch, tmp_path):
    """oc 为 NULL 时不允许把 continue_prompt 误判成续跑指令。"""
    import utils.batch_engine as eng
    sid = _seed_standalone(db_conn, user_id)
    claim_guard(sid)
    worker = _worker(monkeypatch)
    seen = {}

    def create_session(directory='', title=''):
        seen['directory'] = directory
        return 'oc-fresh'

    def send_message(oc, prompt, directory='', agent=None, model=None, **k):
        seen['prompt'] = prompt

    _patch_oc(monkeypatch,
              create_session=staticmethod(create_session),
              send_message=staticmethod(send_message),
              list_messages=staticmethod(lambda oc, directory='': [_DONE_MSG]))
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))

    worker._run_one({'id': sid, 'user_id': user_id, 'batch_id': None,
                     'batch_input_file': None, 'input_files': None,
                     'scan_task_id': None, 'opencode_session_id': None,
                     'workspace_path': str(tmp_path),
                     'continue_prompt': PROMPT, 'agent': '', 'model': ''})

    assert seen['directory'] == str(tmp_path)     # 新建了工作区绑定的 oc 会话
    assert PROMPT in seen['prompt']
