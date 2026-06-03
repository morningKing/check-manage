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


def test_run_one_invokes_scan_hook_on_success(monkeypatch):
    import utils.batch_engine as eng
    calls = {}
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **k: '/tmp/ws')
    fake_oc = MagicMock()
    fake_oc.create_session.return_value = 'oc-1'
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    w = eng.BatchWorker()
    monkeypatch.setattr(w, '_fetch_batch_prompt', lambda b: 'prompt')
    monkeypatch.setattr(w, '_set_opencode_id', lambda *a: None)
    monkeypatch.setattr(w, '_await_finished', lambda *a, **k: ('preview', {'role': 'assistant', 'content': [{'type': 'text', 'text': 'ok'}]}))
    monkeypatch.setattr(w, '_persist_conversation', lambda *a: None)
    monkeypatch.setattr(w, '_mark_done', lambda *a, **k: None)
    import utils.ai_scan_engine as se
    monkeypatch.setattr(se, 'on_child_finished', lambda row, msg, ok: calls.update(row=row, ok=ok))
    w._run_one({'id': 's1', 'user_id': 'u', 'batch_id': 'b', 'batch_input_file': 'd',
                'scan_task_id': 'task-1', 'source_record_id': 'rec-1'})
    assert calls['ok'] is True and calls['row']['source_record_id'] == 'rec-1'


def test_assemble_prompt_appends_contract():
    from utils.ai_scan_engine import assemble_prompt
    task = {'prompt_template': '用方案审核skill审核。',
            'field_mapping': [{'jsonKey': '结论', 'column': '审核结论', 'required': True},
                              {'jsonKey': '意见', 'column': '审核意见', 'required': False}]}
    p = assemble_prompt(task)
    assert 'uploads/record.md' in p
    assert '用方案审核skill审核。' in p
    assert '结论' in p and '意见' in p
    assert 'JSON' in p


def test_build_context_dir_writes_record_md(tmp_path, monkeypatch):
    import utils.ai_scan_engine as se
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    # no file fields → no attachments; record.md rendered from data
    monkeypatch.setattr(se, '_field_labels',
                        lambda coll: {'name': '名称', 'amount': '金额', '审核状态': '审核状态',
                                      '审核结论': '审核结论'})
    monkeypatch.setattr(se, '_file_field_names', lambda coll: [])
    task = {'id': 't1', 'collection': 'orders', 'branch_id': 'main', 'context_fields': {},
            'status_field': '审核状态',
            'field_mapping': [{'jsonKey': '结论', 'column': '审核结论', 'required': True}]}
    rec = {'id': 'rec-1', 'data': {'name': 'A', 'amount': 99, '审核状态': '处理中',
                                   '审核结论': '旧结论'}}
    rel = se.build_context_dir(task, rec)
    from pathlib import Path
    md = (Path(str(tmp_path)) / rel / 'record.md').read_text(encoding='utf-8')
    assert '名称' in md and 'A' in md and '金额' in md and '99' in md
    # status_field + mapped output columns must be excluded from record.md
    assert '处理中' not in md
    assert '审核状态' not in md
    assert '旧结论' not in md
    assert '审核结论' not in md
