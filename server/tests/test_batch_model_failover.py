"""多模型故障切换单测（OC2 阶段五）。

策略契约：ProviderAuthError/APIError（模型不可用类）→ 切换到批配置下一个
未试模型全新派发（retry_count 归零、failed_models 单调增长、continue_prompt
清空、opencode_session_id 置 NULL）；全败 → False（调用方落 failed）；
单模型批/无 batch_id/行缺失 → 不切换。
"""
from unittest.mock import patch, MagicMock

import pytest

import json

from utils.batch_engine import BatchWorker, _TurnFailed


def _worker():
    w = BatchWorker()
    w.POLL_INTERVAL_SEC = 0
    return w


def _db(fetchone_results):
    """极简 DB mock：按调用序返回 fetchone 结果（None=无行）。
    UPDATE 不消费 fetchone。"""
    conn = MagicMock()
    cur = MagicMock()
    it = iter(fetchone_results)

    def _fetchone():
        try:
            return next(it)
        except StopIteration:
            return None

    # 函数里是 with conn.cursor() as cur——cur 实际是 cursor().__enter__() 返回值
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.side_effect = _fetchone
    cur.rowcount = 1
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)
    return patch('utils.batch_engine.get_db', return_value=ctx), cur


def test_failover_switches_to_next_untried_model():
    w = _worker()
    ctx_patch, cur = _db([(['m0/x', 'm1/y', 'm2/z'], 'm0/x', [])])
    with ctx_patch, \
            patch('utils.batch_events.append_event') as ev, \
            patch.object(w, '_close_attempt_for_requeue'):
        ok = w._failover_model('sid1', 'bid1', 'ProviderAuthError: bad key',
                               generation=0)
    assert ok is True
    sql, params = cur.execute.call_args_list[-1].args
    assert "SET model = %s" in sql
    assert params[0] == 'm1/y'                      # 首个未试模型
    assert json.loads(params[1]) == ['m0/x']        # 当前模型入已试清单
    assert "opencode_session_id = NULL" in sql      # fresh dispatch
    assert "retry_count = 0" in sql                 # 新模型满预算
    payload = ev.call_args.kwargs['payload']
    assert payload['reason'].startswith('model_failover:')
    assert payload['from'] == 'm0/x' and payload['to'] == 'm1/y'


def test_failover_all_exhausted_returns_false():
    w = _worker()
    ctx_patch, _ = _db([(['m0/x', 'm1/y'], 'm1/y', ['m0/x'])])
    with ctx_patch:
        assert w._failover_model('sid1', 'bid1', 'ProviderAuthError: x') is False


def test_failover_single_model_batch_returns_false():
    w = _worker()
    ctx_patch, _ = _db([(['m0/x'], 'm0/x', [])])
    with ctx_patch:
        assert w._failover_model('sid1', 'bid1', 'APIError: 503') is False


def test_failover_no_batch_id_returns_false():
    assert _worker()._failover_model('sid1', None, 'APIError: 503') is False


def test_failover_row_missing_returns_false():
    w = _worker()
    ctx_patch, _ = _db([None])
    with ctx_patch:
        assert w._failover_model('sid1', 'bid1', 'APIError') is False


def test_failover_current_not_duplicated_in_tried():
    w = _worker()
    ctx_patch, cur = _db([(['m0/x', 'm1/y'], 'm0/x', ['m0/x'])])
    with ctx_patch, \
            patch('utils.batch_events.append_event'), \
            patch.object(w, '_close_attempt_for_requeue'):
        assert w._failover_model('sid1', 'bid1', 'APIError: 503') is True
    sql, params = cur.execute.call_args_list[-1].args
    assert json.loads(params[1]) == ['m0/x']        # 不重复


def test_failover_overrides_requeue_guards():
    """cancel/pause 置位的行不重排（CAS 语义保留）。"""
    w = _worker()
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (['m0/x', 'm1/y'], 'm0/x', [])
    cur.rowcount = 0                                # UPDATE 0 行=行被并发收口
    conn.cursor.return_value = cur
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)
    with patch('utils.batch_engine.get_db', return_value=ctx):
        assert w._failover_model('sid1', 'bid1', 'APIError: x') is False


def test_turn_failed_signature():
    """_TurnFailed 构造签名（error dict）——接线分类依赖 error.name。"""
    exc = _TurnFailed({'name': 'ProviderAuthError',
                       'data': {'message': 'bad key'}})
    assert exc.name == 'ProviderAuthError'
