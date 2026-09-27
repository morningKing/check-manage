import sys, os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.session_history as session_history


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
    with patch.object(session_history, 'get_db', fake_db):
        block = session_history.render_history_block('sid', exclude_msg_id='m3', max_turns=6)
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
    with patch.object(session_history, 'get_db', fake_db):
        assert session_history.render_history_block('sid', exclude_msg_id='m3') == ''


def test_render_history_block_no_exclude_id_still_scopes_to_session():
    """exclude_msg_id=None（无当前消息可排除）走不带 `AND id != %s` 的分支，
    不能把 None 传给 SQL 的 != 比较——那在三值逻辑下会让整个 WHERE 恒假，
    悄悄返回空历史而不是报错，是最容易被漏测的一个分支。"""
    rows = [('m1', 'user', [{'type': 'text', 'text': '你好'}])]
    conn = MagicMock(); cur = MagicMock()
    cur.fetchall.return_value = rows
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    with patch.object(session_history, 'get_db', fake_db):
        block = session_history.render_history_block('sid')
    assert '你好' in block
