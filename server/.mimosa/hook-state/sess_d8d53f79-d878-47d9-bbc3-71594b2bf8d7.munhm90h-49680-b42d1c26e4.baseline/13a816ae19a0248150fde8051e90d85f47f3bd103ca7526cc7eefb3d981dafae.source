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


def test_claim_one_has_no_status_predicate_at_all():
    """claim_one 的唯一存在理由就是不带状态谓词 —— 手动重跑必须能对
    「已处理」的行再来一次。这里同时锁 SQL 文本和绑定参数两侧，防止谓词
    以硬编码字符串或「排除已处理」等其他形式偷偷混进来。
    """
    import utils.ai_scan_engine as eng
    fake_get_db, cur = _db()
    with patch.object(eng, 'get_db', fake_get_db):
        eng.claim_one(TASK, 'rec-1')
    sql, params = cur.execute.call_args[0]

    # 1) CTE 的 WHERE 只允许出现 id / collection / branch_id 三个条件
    cte_where = sql.split('WHERE', 1)[1].split('FOR UPDATE', 1)[0]
    assert cte_where.count('%s') == 3, f'CTE WHERE 条件数不对: {cte_where}'
    assert 'data' not in cte_where, f'CTE WHERE 不得触碰 data 列: {cte_where}'

    # 2) SQL 文本里不得硬编码任何状态值
    for v in (TASK['pendingValue'], TASK['doneValue'], TASK['failedValue']):
        assert v not in sql, f'SQL 文本硬编码了状态值 {v}'

    # 3) 绑定参数里只有 runningValue（写入用），没有其他状态值
    assert TASK['runningValue'] in params
    for v in (TASK['pendingValue'], TASK['doneValue'], TASK['failedValue']):
        assert v not in params, f'绑定参数里出现了状态值 {v}'


def test_claim_one_returns_none_when_no_row():
    import utils.ai_scan_engine as eng
    fake_get_db, _cur = _db(returning=None)
    with patch.object(eng, 'get_db', fake_get_db):
        assert eng.claim_one(TASK, 'gone') is None
