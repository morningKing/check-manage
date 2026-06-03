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
