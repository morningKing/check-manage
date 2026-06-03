import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pathlib import Path


def test_prepare_workspace_copies_directory(tmp_path, monkeypatch):
    import utils.batch_engine as eng
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    # stage a directory with two files
    staged = tmp_path / 'scan-staging' / 't1' / 'r1'
    (staged / 'attachments').mkdir(parents=True)
    (staged / 'record.md').write_text('hi', encoding='utf-8')
    (staged / 'attachments' / 'doc.txt').write_text('doc', encoding='utf-8')
    ws = eng._prepare_workspace('user-1', 'sess-1', 'scan-staging/t1/r1')
    up = Path(ws) / 'uploads'
    assert (up / 'record.md').read_text(encoding='utf-8') == 'hi'
    assert (up / 'attachments' / 'doc.txt').read_text(encoding='utf-8') == 'doc'


def test_prepare_workspace_single_file_still_works(tmp_path, monkeypatch):
    import utils.batch_engine as eng
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    (tmp_path / 'batch-staging').mkdir(parents=True)
    (tmp_path / 'batch-staging' / 'f.txt').write_text('x', encoding='utf-8')
    ws = eng._prepare_workspace('u', 's', 'batch-staging/f.txt')
    assert (Path(ws) / 'uploads' / 'f.txt').read_text(encoding='utf-8') == 'x'


from unittest.mock import MagicMock, patch
from contextlib import contextmanager


def _mock_db():
    cur = MagicMock()
    cur.fetchone.side_effect = lambda: {'id': 'x'}
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda s, *a: None
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None
    @contextmanager
    def fake():
        yield conn
    return fake, cur


def test_create_batch_stamps_scan_columns():
    import utils.batch_repo as repo
    fake, cur = _mock_db()
    with patch('utils.batch_repo.get_db', fake):
        repo.create_batch('user-1', name='n', prompt='p', template_id=None,
                          files=[{'name': 'r1', 'path': 'scan-staging/t/r1', 'recordId': 'rec-1'}],
                          scan_task_id='task-1')
    # the child INSERT must include scan_task_id + source_record_id values
    inserts = [c for c in cur.execute.call_args_list if 'INSERT INTO ai_chat_sessions' in str(c.args[0])]
    assert inserts
    assert any('scan_task_id' in str(c.args[0]) for c in inserts)
    flat = [v for c in inserts for v in (c.args[1] if len(c.args) > 1 else ())]
    assert 'task-1' in flat and 'rec-1' in flat
