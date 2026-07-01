# server/tests/test_kefu_repo.py
"""Unit tests for kefu_repo — mocked DB, no real PostgreSQL or OpenCode."""
import json
from unittest.mock import patch, MagicMock
import utils.kefu_repo as repo


def _cm(conn):
    from contextlib import contextmanager

    @contextmanager
    def cm():
        yield conn

    return cm()


def test_create_kefu_session_inserts_row_with_visitor(mock_conn, mock_cursor):
    instance = {
        'id': 'kf_1', 'slug': 'presale', 'name': '售前',
        'agent': '', 'model': '', 'system_prompt': '你是售前助手',
        'bot_user_id': 'kefu-bot',
    }
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)), \
         patch('utils.kefu_repo.create_session_workspace', return_value='/ws/kf'), \
         patch('utils.kefu_repo.write_opencode_config'), \
         patch('utils.kefu_repo.generate_token', return_value='tok123'), \
         patch('utils.kefu_repo.OpenCodeClient') as OC, \
         patch('utils.kefu_repo._inject_system_prompt') as inj:
        OC.return_value.create_session.return_value = 'oc_sid_1'
        out = repo.create_kefu_session(instance, 'visitor-abc')
    assert out['id'].startswith('sess_')
    # 校验插入语句带上了 visitor_id 与 bot 用户
    insert_sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'ai_chat_sessions' in insert_sql
    inserted_params = [c.args[1] for c in mock_cursor.execute.call_args_list if c.args[1]]
    flat = [p for params in inserted_params for p in params]
    assert 'visitor-abc' in flat
    assert 'kefu-bot' in flat
    assert 'kf_1' in flat  # kefu_instance_id present in the insert
    inj.assert_called_once()  # 护栏被注入工作区


def test_create_kefu_session_injects_guardrail(mock_conn, mock_cursor):
    """_inject_system_prompt is called with the workspace path and instance."""
    instance = {
        'id': 'kf_2', 'slug': 'aftersale', 'name': '售后',
        'agent': '', 'model': 'anthropic/claude-3-5-sonnet', 'system_prompt': '你是售后',
        'bot_user_id': 'kefu-bot',
    }
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)), \
         patch('utils.kefu_repo.create_session_workspace', return_value='/ws/kf2'), \
         patch('utils.kefu_repo.write_opencode_config'), \
         patch('utils.kefu_repo.generate_token', return_value='tok456'), \
         patch('utils.kefu_repo.OpenCodeClient') as OC, \
         patch('utils.kefu_repo._inject_system_prompt') as inj:
        OC.return_value.create_session.return_value = 'oc_sid_2'
        out = repo.create_kefu_session(instance, 'visitor-xyz')
    inj.assert_called_once_with('/ws/kf2', instance)
    assert out['title'] == '客服会话'


def test_create_kefu_session_calls_ensure_bot_user(mock_conn, mock_cursor):
    """create_kefu_session must call ensure_bot_user() on every invocation
    so a manual role edit on kefu-bot cannot silently widen access."""
    instance = {
        'id': 'kf_spot', 'slug': 'test', 'name': '测试',
        'agent': '', 'model': '', 'system_prompt': '',
        'bot_user_id': 'kefu-bot',
    }
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)), \
         patch('utils.kefu_repo.create_session_workspace', return_value='/ws/spot'), \
         patch('utils.kefu_repo.write_opencode_config'), \
         patch('utils.kefu_repo.generate_token', return_value='tok_spot'), \
         patch('utils.kefu_repo.OpenCodeClient') as OC, \
         patch('utils.kefu_repo._inject_system_prompt'), \
         patch('utils.kefu_repo.ensure_bot_user') as mock_ensure:
        OC.return_value.create_session.return_value = 'oc_spot'
        repo.create_kefu_session(instance, 'visitor-spot')
    mock_ensure.assert_called_once()


def test_load_kefu_session_returns_none_for_wrong_visitor(mock_conn, mock_cursor):
    """load_kefu_session returns None when row not found (mismatched visitor)."""
    mock_cursor.fetchone.return_value = None
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        out = repo.load_kefu_session('sess_1', 'wrong-visitor')
    assert out is None
    # the query must filter by visitor_id AND kefu_instance_id IS NOT NULL
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'visitor_id' in sql and 'kefu_instance_id IS NOT NULL' in sql
