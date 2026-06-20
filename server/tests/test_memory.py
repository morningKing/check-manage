import sys, os
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.memory as mem

def setup_function():
    mem.reset_memory_singleton()

def test_get_memory_none_when_disabled():
    with patch.object(mem, 'get_ai_settings', return_value={'mem0Enabled': False, 'apiKey': 'sk'}):
        assert mem.get_memory() is None

def test_search_empty_when_disabled():
    with patch.object(mem, 'get_ai_settings', return_value={'mem0Enabled': False, 'apiKey': 'sk'}):
        assert mem.search_memory('u', 'q') == []

def test_add_noop_when_disabled():
    with patch.object(mem, 'get_ai_settings', return_value={'mem0Enabled': False, 'apiKey': 'sk'}):
        mem.add_memory('u', [{'role': 'user', 'content': 'x'}])  # must not raise

def test_search_scopes_by_user_and_unwraps_results():
    fake = MagicMock()
    fake.search.return_value = {'results': [{'id': '1', 'memory': '喜欢 Python'}]}
    with patch.object(mem, 'get_memory', return_value=fake):
        out = mem.search_memory('alice', '技术', limit=3)
    fake.search.assert_called_once_with(query='技术', filters={'user_id': 'alice'}, limit=3)
    assert out == [{'id': '1', 'memory': '喜欢 Python'}]

def test_add_swallows_errors():
    fake = MagicMock(); fake.add.side_effect = RuntimeError('boom')
    with patch.object(mem, 'get_memory', return_value=fake):
        mem.add_memory('alice', [{'role': 'user', 'content': 'x'}])  # must not raise
