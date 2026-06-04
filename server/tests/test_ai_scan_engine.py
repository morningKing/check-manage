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


def test_prepare_workspace_retries_on_permission_error(tmp_path, monkeypatch):
    import utils.batch_engine as eng
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    staged = tmp_path / 'scan-staging' / 't1' / 'r1'
    (staged / 'attachments').mkdir(parents=True)
    (staged / 'record.md').write_text('hi', encoding='utf-8')

    calls = {'n': 0}
    real_copytree = eng.shutil.copytree

    def flaky(src, dst, *args, **kwargs):
        # shutil.copytree recurses into subdirs via the same name; only count
        # the top-level call into our staged dir, not internal recursion.
        if str(src) == str(staged):
            calls['n'] += 1
            if calls['n'] == 1:
                raise PermissionError(13, 'Permission denied')
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(eng.shutil, 'copytree', flaky)
    monkeypatch.setattr(eng.time, 'sleep', lambda *a, **k: None)

    ws = eng._prepare_workspace('user-1', 'sess-1', 'scan-staging/t1/r1')
    assert calls['n'] == 2
    assert (Path(ws) / 'uploads' / 'record.md').read_text(encoding='utf-8') == 'hi'


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
    task = {'promptTemplate': '用方案审核skill审核。',
            'fieldMapping': [{'jsonKey': '结论', 'column': '审核结论', 'required': True},
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
    task = {'id': 't1', 'collection': 'orders', 'branchId': 'main', 'contextFields': {},
            'statusField': '审核状态',
            'fieldMapping': [{'jsonKey': '结论', 'column': '审核结论', 'required': True}]}
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


def test_claim_builds_pending_predicate_and_running_update():
    import utils.ai_scan_engine as se
    fake, cur = _mock_db()
    cur.fetchall = MagicMock(return_value=[('rec-1', {'name': 'A'})])
    task = {'id': 't1', 'collection': 'orders', 'branchId': 'main',
            'statusField': '审核状态', 'pendingValue': '未审核', 'runningValue': '处理中',
            'extraFilter': {}, 'maxRecordsPerScan': 5}
    with patch('utils.ai_scan_engine.get_db', fake):
        claimed = se.claim_records(task)
    sql = str(cur.execute.call_args_list[-1].args[0])
    assert 'FOR UPDATE SKIP LOCKED' in sql and 'UPDATE dynamic_data' in sql
    assert claimed == [{'id': 'rec-1', 'data': {'name': 'A'}}]


def test_run_task_creates_batch_for_claimed(monkeypatch):
    import utils.ai_scan_engine as se
    monkeypatch.setattr(se, 'claim_records', lambda task: [{'id': 'rec-1', 'data': {}}])
    monkeypatch.setattr(se, 'build_context_dir', lambda task, rec: f"scan-staging/{task['id']}/{rec['id']}")
    captured = {}
    monkeypatch.setattr(se, 'create_batch', lambda *a, **k: captured.update(kwargs=k, args=a) or {'batch': {}})
    monkeypatch.setattr(se, 'mark_run', lambda *a, **k: None)
    task = {'id': 't1', 'name': '审核', 'ownerUserId': 'u', 'collection': 'orders',
            'promptTemplate': 'p', 'fieldMapping': []}
    se.run_task(task)
    assert captured['kwargs']['scan_task_id'] == 't1'
    assert captured['kwargs']['files'][0]['recordId'] == 'rec-1'


def test_run_task_reverts_all_claimed_on_failure(monkeypatch):
    import utils.ai_scan_engine as se
    claimed = [{'id': 'rec-1', 'data': {}}, {'id': 'rec-2', 'data': {}}, {'id': 'rec-3', 'data': {}}]
    monkeypatch.setattr(se, 'claim_records', lambda task: claimed)
    # build_context_dir fails on the 3rd record
    calls = {'n': 0}
    def boom(task, rec):
        calls['n'] += 1
        if calls['n'] == 3:
            raise RuntimeError('stage failed')
        return f"scan-staging/{task['id']}/{rec['id']}"
    monkeypatch.setattr(se, 'build_context_dir', boom)
    monkeypatch.setattr(se, 'create_batch', lambda *a, **k: {'batch': {}})
    monkeypatch.setattr(se, 'mark_run', lambda *a, **k: None)
    reverted = {}
    monkeypatch.setattr(se, '_revert_claimed', lambda task, ids: reverted.update(ids=ids))
    import pytest
    task = {'id': 't1', 'name': 'n', 'ownerUserId': 'u', 'collection': 'c',
            'promptTemplate': 'p', 'fieldMapping': []}
    with pytest.raises(RuntimeError):
        se.run_task(task)
    assert set(reverted['ids']) == {'rec-1', 'rec-2', 'rec-3'}  # ALL claimed reverted


def test_run_task_zero_claimed_no_batch(monkeypatch):
    import utils.ai_scan_engine as se
    monkeypatch.setattr(se, 'claim_records', lambda task: [])
    called = {'batch': False}
    monkeypatch.setattr(se, 'create_batch', lambda *a, **k: called.update(batch=True))
    monkeypatch.setattr(se, 'mark_run', lambda *a, **k: None)
    se.run_task({'id': 't1', 'name': 'n', 'ownerUserId': 'u', 'collection': 'c',
                 'promptTemplate': 'p', 'fieldMapping': []})
    assert called['batch'] is False


def test_is_due_logic():
    from utils.ai_scan_scheduler import _is_due
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    assert _is_due({'lastRunAt': None, 'scheduleIntervalMinutes': 15}, now) is True
    assert _is_due({'lastRunAt': (now - timedelta(minutes=20)).isoformat(),
                    'scheduleIntervalMinutes': 15}, now) is True
    assert _is_due({'lastRunAt': (now - timedelta(minutes=5)).isoformat(),
                    'scheduleIntervalMinutes': 15}, now) is False
