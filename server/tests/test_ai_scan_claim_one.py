import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

TASK = {
    'id': 'st-1', 'collection': 'orders', 'branchId': 'main',
    'statusField': 'aiStatus', 'pendingValue': '待处理',
    'runningValue': '处理中', 'doneValue': '已处理', 'failedValue': '处理失败',
    'maxRecordsPerScan': 20, 'fieldMapping': [],
}


def _db(returning=('rec-1', {'a': 1})):
    cur = MagicMock()
    cur.fetchone.return_value = returning
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda s, *a: None

    @contextmanager
    def fake_get_db():
        yield conn

    return fake_get_db, cur


def test_claim_one_returns_record_and_flips_status():
    import utils.ai_scan_engine as eng
    fake_get_db, cur = _db()
    with patch.object(eng, 'get_db', fake_get_db):
        rec = eng.claim_one(TASK, 'rec-1')
    assert rec == {'id': 'rec-1', 'data': {'a': 1}}
    sql, params = cur.execute.call_args[0]
    assert 'FOR UPDATE SKIP LOCKED' in sql
    assert '处理中' in params
    assert 'rec-1' in params


def test_claim_one_ignores_pending_value_predicate():
    """手动重跑必须能对「已处理」的行再来一次，所以 SQL 里不能有状态谓词。"""
    import utils.ai_scan_engine as eng
    fake_get_db, cur = _db()
    with patch.object(eng, 'get_db', fake_get_db):
        eng.claim_one(TASK, 'rec-1')
    sql, params = cur.execute.call_args[0]
    assert '待处理' not in params


def test_claim_one_returns_none_when_no_row():
    import utils.ai_scan_engine as eng
    fake_get_db, _cur = _db(returning=None)
    with patch.object(eng, 'get_db', fake_get_db):
        assert eng.claim_one(TASK, 'gone') is None
