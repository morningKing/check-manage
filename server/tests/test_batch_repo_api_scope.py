import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _capture_db():
    """mock get_db；返回 (fake_get_db, cur)，cur 上能读到执行过的 SQL 与参数。"""
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = {'n': 0}
    cur.rowcount = 0
    ctx = MagicMock()
    ctx.__enter__ = lambda s: cur
    ctx.__exit__ = lambda s, *a: None
    conn = MagicMock()
    conn.cursor.return_value = ctx

    @contextmanager
    def fake_get_db():
        yield conn

    return fake_get_db, cur


def _sqls(cur):
    return [c[0][0] for c in cur.execute.call_args_list]


def _params(cur):
    return [c[0][1] for c in cur.execute.call_args_list if len(c[0]) > 1]


# ---------- api_key_id 过滤 ----------

def test_list_batches_without_api_key_id_keeps_old_behavior():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    with patch.object(repo, 'get_db', fake):
        repo.list_batches('user-1', page=1, page_size=20)
    for sql in _sqls(cur):
        assert 'api_key_id' not in sql


def test_list_batches_with_api_key_id_filters():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    with patch.object(repo, 'get_db', fake):
        repo.list_batches('user-1', page=1, page_size=20, api_key_id='ak-1')
    assert all('api_key_id = %s' in s for s in _sqls(cur))
    assert any('ak-1' in p for p in _params(cur))


def test_get_batch_detail_with_api_key_id_filters():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchone.return_value = None
    with patch.object(repo, 'get_db', fake):
        assert repo.get_batch_detail('user-1', 'b-1', api_key_id='ak-1') is None
    assert 'api_key_id = %s' in _sqls(cur)[0]


def test_delete_batch_with_api_key_id_filters():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    with patch.object(repo, 'get_db', fake):
        repo.delete_batch('user-1', 'b-1', api_key_id='ak-1')
    assert 'api_key_id = %s' in _sqls(cur)[0]


def test_reset_failed_with_api_key_id_filters():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    with patch.object(repo, 'get_db', fake):
        repo.reset_failed_to_pending('user-1', 'b-1', api_key_id='ak-1')
    assert any('api_key_id = %s' in s for s in _sqls(cur))


def test_create_batch_writes_api_key_id():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchone.return_value = {'id': 'b-1'}
    with patch.object(repo, 'get_db', fake):
        repo.create_batch('user-1', name='n', prompt='p', template_id=None,
                          files=[{'name': 'a.pdf', 'path': 'batch-staging/user-1/s/a.pdf'}],
                          api_key_id='ak-1')
    insert_sql = _sqls(cur)[0]
    assert 'api_key_id' in insert_sql
    assert 'ak-1' in _params(cur)[0]


# ---------- get_batch_results ----------

def test_get_batch_results_uses_basename_not_full_path():
    """batch_input_file 存的是工作区相对路径，直接返回会泄漏内部 userId。"""
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchall.return_value = [
        {'batch_input_file': 'batch-staging/user-42/abc/订单A.pdf',
         'status': 'completed', 'error_message': None,
         'content': [{'type': 'text', 'text': '第一行\n第二行'}]},
    ]
    with patch.object(repo, 'get_db', fake):
        results = repo.get_batch_results('b-1')
    assert results[0]['name'] == '订单A.pdf'
    assert 'user-42' not in str(results[0])


def test_get_batch_results_returns_full_text_not_first_line():
    """output 必须是完整回复，不能是 last_message_preview 的首行。"""
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchall.return_value = [
        {'batch_input_file': 'batch-staging/u/s/a.pdf',
         'status': 'completed', 'error_message': None,
         'content': [{'type': 'text', 'text': '第一行\n第二行'}]},
    ]
    with patch.object(repo, 'get_db', fake):
        results = repo.get_batch_results('b-1')
    assert '第二行' in results[0]['output']


def test_get_batch_results_concatenates_multiple_text_parts():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchall.return_value = [
        {'batch_input_file': 'batch-staging/u/s/a.pdf',
         'status': 'completed', 'error_message': None,
         'content': [{'type': 'text', 'text': 'AAA'},
                     {'type': 'tool_use', 'name': 'read'},
                     {'type': 'text', 'text': 'BBB'}]},
    ]
    with patch.object(repo, 'get_db', fake):
        results = repo.get_batch_results('b-1')
    assert results[0]['output'] == 'AAA\nBBB'   # tool_use 部分被丢弃


def test_get_batch_results_pending_child_has_null_output():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchall.return_value = [
        {'batch_input_file': 'batch-staging/u/s/a.pdf',
         'status': 'pending', 'error_message': None, 'content': None},
    ]
    with patch.object(repo, 'get_db', fake):
        results = repo.get_batch_results('b-1')
    assert results[0] == {'name': 'a.pdf', 'status': 'pending',
                          'output': None, 'error': None}


def test_get_batch_results_failed_child_carries_error():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchall.return_value = [
        {'batch_input_file': 'batch-staging/u/s/a.pdf',
         'status': 'failed', 'error_message': '超时', 'content': None},
    ]
    with patch.object(repo, 'get_db', fake):
        results = repo.get_batch_results('b-1')
    assert results[0]['status'] == 'failed'
    assert results[0]['error'] == '超时'
    assert results[0]['output'] is None
