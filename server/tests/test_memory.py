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

def test_render_memory_block_formats_lines():
    block = mem.render_memory_block([{'memory': '喜欢 Python'}, {'memory': '在用 PostgreSQL'}, {'memory': ''}])
    assert '喜欢 Python' in block and '在用 PostgreSQL' in block
    assert block.startswith('[关于当前用户的长期记忆')
    assert block.endswith('\n\n')

def test_render_memory_block_empty_returns_empty():
    assert mem.render_memory_block([]) == ''

def test_extract_skips_noninteractive_session():
    # session row: (user_id, batch_id, scan_task_id) — batch session must be skipped
    conn = MagicMock(); cur = MagicMock()
    cur.fetchone.return_value = ('u1', 'batch-9', None)
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    called = {'n': 0}
    with patch.object(mem, 'get_db', fake_db), \
         patch.object(mem, 'add_memory', lambda *a, **k: called.__setitem__('n', called['n'] + 1)):
        mem.extract_from_turn('sid', state={'turn_msg_id': 'm', 'text': 'hi'}, _sync=True)
    assert called['n'] == 0  # batch session → no extraction

def test_extract_interactive_calls_add():
    conn = MagicMock(); cur = MagicMock()
    # 1st fetchone = session row (interactive: no batch/scan); 2nd = last user message content (JSONB list)
    cur.fetchone.side_effect = [('u1', None, None), ([{'type': 'text', 'text': '今天天气如何'}],)]
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    captured = {}
    with patch.object(mem, 'get_db', fake_db), \
         patch.object(mem, '_turn_text', return_value='晴天'), \
         patch.object(mem, 'add_memory', lambda uid, msgs: captured.update(uid=uid, msgs=msgs)):
        mem.extract_from_turn('sid', state={'turn_msg_id': 'm'}, _sync=True)
    assert captured['uid'] == 'u1'
    assert {'role': 'user', 'content': '今天天气如何'} in captured['msgs']
    assert {'role': 'assistant', 'content': '晴天'} in captured['msgs']
