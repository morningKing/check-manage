import sys, os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import routes.ai_chat as ai_chat

def test_render_history_block_excludes_current_and_formats():
    rows = [
        ('m1', 'user', [{'type': 'text', 'text': '你好'}]),
        ('m2', 'assistant', [{'type': 'text', 'text': '你好，有什么可以帮你'}]),
    ]
    conn = MagicMock(); cur = MagicMock()
    cur.fetchall.return_value = rows
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    with patch.object(ai_chat, 'get_db', fake_db):
        block = ai_chat._render_history_block('sid', exclude_msg_id='m3', max_turns=6)
    assert '你好' in block and '你好，有什么可以帮你' in block
    assert block.startswith('[此前对话摘要')
    assert block.endswith('\n\n')

def test_render_history_block_empty_returns_empty():
    conn = MagicMock(); cur = MagicMock()
    cur.fetchall.return_value = []
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    with patch.object(ai_chat, 'get_db', fake_db):
        assert ai_chat._render_history_block('sid', exclude_msg_id='m3') == ''

def test_recover_creates_session_injects_history_and_resends():
    client = MagicMock()
    client.create_session.return_value = 'new-oc-sid'
    sent = {}
    client.send_prompt_async.side_effect = lambda oc, content, **kw: sent.update(oc=oc, content=content)
    conn = MagicMock(); cur = MagicMock()
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    with patch.object(ai_chat, 'get_db', fake_db), \
         patch.object(ai_chat, '_render_history_block', return_value='[此前对话摘要]\n用户: 你好\n\n'):
        new_id = ai_chat._recover_session_and_resend(
            client, sid='sid', workspace_path='/ws', current_msg_id='m3',
            prompt='原始PROMPT', model='m', agent='', agent_parts=[])
    assert new_id == 'new-oc-sid'
    client.create_session.assert_called_once()
    assert any('opencode_session_id' in str(c.args[0]) for c in cur.execute.call_args_list)
    assert sent['oc'] == 'new-oc-sid'
    assert '此前对话摘要' in sent['content'] and '原始PROMPT' in sent['content']
