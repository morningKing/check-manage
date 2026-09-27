from contextlib import contextmanager
from unittest.mock import patch
import utils.kefu_repo as repo


def _cm(conn):
    @contextmanager
    def cm():
        yield conn
    return cm()


def test_create_faq_inserts_scoped_to_instance(mock_conn, mock_cursor):
    mock_cursor.fetchone.return_value = (
        'faq_1', 'kf_1', 'Q?', 'A', 'billing', 0, 0, True)
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        out = repo.create_faq('kf_1', {'question': 'Q?', 'answer': 'A', 'category': 'billing'})
    assert out['id'] == 'faq_1' and out['instance_id'] == 'kf_1'
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'kefu_faq_items' in sql
    params = [p for c in mock_cursor.execute.call_args_list if c.args[1] for p in c.args[1]]
    assert 'kf_1' in params and 'Q?' in params


def test_increment_click_sql_scoped_and_enabled(mock_conn, mock_cursor):
    mock_cursor.rowcount = 1
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        hit = repo.increment_faq_click('kf_1', 'faq_1')
    assert hit is True
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'click_count = click_count + 1' in sql
    assert 'enabled' in sql and 'instance_id' in sql


def test_reorder_writes_sort_order_by_index(mock_conn, mock_cursor):
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        repo.reorder_faq('kf_1', ['faq_b', 'faq_a'])
    calls = [c.args for c in mock_cursor.execute.call_args_list if c.args[1]]
    # faq_b → sort_order 0, faq_a → sort_order 1, both scoped to kf_1
    flat = [c[1] for c in calls]
    assert (0, 'faq_b', 'kf_1') in flat and (1, 'faq_a', 'kf_1') in flat


def test_list_faq_public_omits_private_fields(mock_conn, mock_cursor):
    mock_cursor.fetchall.return_value = [('faq_1', 'Q?', 'A', 'billing')]
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        items = repo.list_faq_public('kf_1')
    assert len(items) == 1
    item = items[0]
    assert set(item.keys()) == {'id', 'question', 'answer', 'category'}
    assert item['id'] == 'faq_1' and item['question'] == 'Q?' and item['answer'] == 'A' and item['category'] == 'billing'
    assert 'click_count' not in item
    assert 'enabled' not in item
    assert 'instance_id' not in item
