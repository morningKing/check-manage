"""utils/ai_session_repo.py —— standalone /v1/ai-sessions 数据层（此前零测试）。

重点回归：
- get_session_for_owner 的 output 门：仅 status='completed' 回 content；
- 归属双条件（user_id + api_key_id）拼进 WHERE；
- cancel_session 终态拒绝（ValueError）、未命中返回 None。
"""
import sys, os
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.ai_session_repo as repo


def _mock_db(fetchone=None):
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda s, *a: None
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None

    @contextmanager
    def fake():
        yield conn
    return fake, cur, conn


ROW = {'id': 's1', 'title': 't', 'status': 'running', 'agent': None,
       'model': None, 'error_message': None, 'created_at': '2026-09-19',
       'last_active_at': '2026-09-19', 'input_files': [{'name': 'a.txt', 'path': 'x'}],
       'content': [{'type': 'text', 'text': '半截输出'}]}


def test_create_inserts_pending_row_with_stashed_prompt():
    fake, cur, conn = _mock_db(fetchone=dict(ROW))
    with patch('utils.ai_session_repo.get_db', fake):
        repo.create_session('u1', prompt='做点什么', agent='a', model='m',
                            title='标题', api_key_id='k1', files=[{'name': 'a'}])
    sql, params = cur.execute.call_args.args
    assert "INSERT INTO ai_chat_sessions" in sql
    assert "'pending'" in sql
    # prompt 暂存在 continue_prompt 列（worker 认领时读取）
    assert params[3] == '做点什么'
    assert params[6] == 'k1'
    conn.commit.assert_called_once()


def test_get_owner_scopes_by_user_and_key():
    fake, cur, conn = _mock_db(fetchone=dict(ROW))
    with patch('utils.ai_session_repo.get_db', fake), \
         patch('utils.ai_session_repo.get_session_usage', lambda sid: {'total': 0}):
        repo.get_session_for_owner('s1', 'u1', 'k1')
    sql, params = cur.execute.call_args.args
    assert 's.user_id = %s' in sql and 's.api_key_id = %s' in sql
    assert params == ('s1', 'u1', 'k1')


def test_get_output_gate_only_for_completed():
    """运行中不回 output（半截文本防泄漏门），失败回 error。"""
    fake, cur, _ = _mock_db(fetchone=dict(ROW, status='running'))
    with patch('utils.ai_session_repo.get_db', fake), \
         patch('utils.ai_session_repo.get_session_usage', lambda sid: {}):
        out = repo.get_session_for_owner('s1', 'u1', 'k1')
    assert out['output'] is None
    assert out['status'] == 'running'

    fake, cur, _ = _mock_db(fetchone=dict(ROW, status='completed'))
    with patch('utils.ai_session_repo.get_db', fake), \
         patch('utils.ai_session_repo.get_session_usage', lambda sid: {}):
        out = repo.get_session_for_owner('s1', 'u1', 'k1')
    assert out['output'] == '半截输出'

    fake, cur, _ = _mock_db(fetchone=None)
    with patch('utils.ai_session_repo.get_db', fake):
        assert repo.get_session_for_owner('nope', 'u1', 'k1') is None


def test_get_files_echo_names_only():
    """files 只回显 name，不回显内部暂存路径。"""
    fake, cur, _ = _mock_db(fetchone=dict(ROW, status='completed'))
    with patch('utils.ai_session_repo.get_db', fake), \
         patch('utils.ai_session_repo.get_session_usage', lambda sid: {}):
        out = repo.get_session_for_owner('s1', 'u1', 'k1')
    assert out['files'] == [{'name': 'a.txt'}]


def test_cancel_rejects_terminal_sessions():
    fake, cur, _ = _mock_db(fetchone=('completed',))
    with patch('utils.ai_session_repo.get_db', fake):
        with pytest.raises(ValueError):
            repo.cancel_session('s1', 'u1', 'k1')


def test_cancel_missing_returns_none():
    fake, cur, _ = _mock_db(fetchone=None)
    with patch('utils.ai_session_repo.get_db', fake):
        assert repo.cancel_session('s1', 'u1', 'k1') is None


def test_cancel_running_sets_flag_and_returns_detail():
    fake, cur, conn = _mock_db(fetchone=('running',))
    detail = {'id': 's1', 'status': 'running', 'output': None}
    with patch('utils.ai_session_repo.get_db', fake), \
         patch('utils.ai_session_repo.get_session_usage', lambda sid: {}), \
         patch.object(repo, 'get_session_for_owner', lambda *a: detail):
        out = repo.cancel_session('s1', 'u1', 'k1')
    assert out == detail
    upd = [c for c in cur.execute.call_args_list if 'UPDATE' in str(c.args[0])]
    assert 'cancel_requested = true' in upd[0].args[0]
