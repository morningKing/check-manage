from contextlib import contextmanager
from unittest.mock import patch
import utils.kefu_repo as repo


def _cm(conn):
    @contextmanager
    def cm():
        yield conn
    return cm()


def test_load_kefu_session_includes_human_takeover(mock_conn, mock_cursor):
    mock_cursor.fetchone.return_value = (
        'sess_1', 'user-bot', 'oc_1', 'active', '/ws', 'kf_1', True)
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        sess = repo.load_kefu_session('sess_1', 'v1')
    assert sess[6] is True  # human_takeover at index 6
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'human_takeover' in sql


def test_set_needs_human_updates_scoped(mock_conn, mock_cursor):
    mock_cursor.rowcount = 1
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        ok = repo.set_needs_human('sess_1', True)
    assert ok is True
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'needs_human' in sql and 'kefu_instance_id IS NOT NULL' in sql


def test_takeover_sets_flags_and_clears_needs_human(mock_conn, mock_cursor):
    mock_cursor.rowcount = 1
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        ok = repo.takeover_session('sess_1', 'user-admin')
    assert ok is True
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'human_takeover=true' in sql and 'needs_human=false' in sql
    params = [p for c in mock_cursor.execute.call_args_list if c.args[1] for p in c.args[1]]
    assert 'user-admin' in params and 'sess_1' in params


def test_release_clears_flags(mock_conn, mock_cursor):
    mock_cursor.rowcount = 1
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        ok = repo.release_session('sess_1')
    assert ok is True
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'human_takeover=false' in sql and 'human_agent_id=NULL' in sql


def test_insert_human_message_marks_meta(mock_conn, mock_cursor):
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        mid = repo.insert_human_message('sess_1', '你好', 'user-admin')
    assert mid.startswith('msg_')
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'ai_chat_messages' in sql and "'assistant'" in sql
    params = [p for c in mock_cursor.execute.call_args_list if c.args[1] for p in c.args[1]]
    joined = ' '.join(str(p) for p in params)
    assert 'human' in joined and 'user-admin' in joined  # meta author + agent id


def test_list_sessions_admin_filters(mock_conn, mock_cursor):
    mock_cursor.fetchall.return_value = []
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        repo.list_kefu_sessions_admin(instance_id='kf_1', needs_human=True)
    call = mock_cursor.execute.call_args_list[-1]
    sql, params = call.args[0], call.args[1]
    assert 'kefu_instance_id=%s' in sql and 'needs_human=%s' in sql
    assert 'kf_1' in params and True in params


def test_get_messages_returns_meta(mock_conn, mock_cursor):
    mock_cursor.fetchall.return_value = [
        ('msg_1', 'assistant', [{'type': 'text', 'text': 'hi'}],
         {'author': 'human'}, None)]
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        out = repo.get_messages('sess_1')
    assert out[0]['role'] == 'assistant' and out[0]['meta'] == {'author': 'human'}
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'meta' in sql and 'ai_chat_messages' in sql
