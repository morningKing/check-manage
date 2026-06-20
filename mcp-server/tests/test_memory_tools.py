import sys, os
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from context import ToolContext
from tools import memory as memtool

CTX = ToolContext(session_id='s', user_id='alice', role='developer')

def test_search_scopes_to_user():
    with patch('tools.memory.memory_client.search', return_value=[{'id': '1', 'memory': '喜欢 Python'}]) as s:
        out = memtool.handle_search({'query': '技术', 'limit': 3}, CTX)
    s.assert_called_once_with('alice', '技术', 3)
    assert '喜欢 Python' in out

def test_add_uses_ctx_user():
    with patch('tools.memory.memory_client.add') as a:
        memtool.handle_add({'text': '记住X'}, CTX)
    a.assert_called_once_with('alice', '记住X')

def test_delete():
    with patch('tools.memory.memory_client.delete') as d:
        memtool.handle_delete({'memoryId': 'm1'}, CTX)
    d.assert_called_once_with('m1')
