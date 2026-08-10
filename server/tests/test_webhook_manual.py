import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# webhook_rules 查询返回的列顺序，与 fire_webhook_rule 里的 SELECT 对齐
RULE_ROW = ('wh-1', '推送外部', 'https://example.com/hook', 'sec', 10, 1)


def _db(rule_row=RULE_ROW):
    cur = MagicMock()
    cur.fetchone.return_value = rule_row
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def fake_get_db():
        yield conn

    return fake_get_db, cur


def test_build_payload_manual_carries_action_and_params():
    from utils.webhook_engine import _build_payload
    payload = _build_payload(
        'after', 'manual', 'orders', 'rec-1', None, {'status': '待审核'},
        'admin', 'wh-1', '推送外部', 'main', None,
        action_id='ra-1', action_label='推送外部', params={'reason': '缺料'},
    )
    assert payload['event'] == 'manual'
    assert payload['collection'] == 'orders'
    assert payload['recordId'] == 'rec-1'
    assert payload['record'] == {'status': '待审核'}
    assert payload['actionId'] == 'ra-1'
    assert payload['actionLabel'] == '推送外部'
    assert payload['params'] == {'reason': '缺料'}


def test_fire_webhook_rule_calls_single_webhook_with_rule_config():
    import utils.webhook_engine as we
    fake_get_db, _cur = _db()
    with patch('db.get_db', fake_get_db), \
         patch.object(we, '_fire_single_webhook',
                      return_value={'success': True, 'logId': 'l1',
                                    'responseStatus': 200, 'errorMessage': None,
                                    'retryCount': 0, 'responseBody': '{"a":1}'}) as m:
        res = we.fire_webhook_rule(
            'wh-1', 'orders', 'rec-1', {'status': '待审核'}, 'admin',
            {'reason': '缺料'}, 'main', action_id='ra-1', action_label='推送外部')
    assert res['success'] is True
    kwargs = m.call_args
    # 位置参数顺序: rule_id, rule_name, url, secret, event_type, payload, timeout, retries
    assert kwargs[0][0] == 'wh-1'
    assert kwargs[0][2] == 'https://example.com/hook'
    assert kwargs[0][4] == 'manual'
    assert kwargs[0][6] == 10
    assert kwargs[0][7] == 1


def test_fire_webhook_rule_missing_rule_raises():
    import utils.webhook_engine as we
    fake_get_db, _cur = _db(rule_row=None)
    with patch('db.get_db', fake_get_db):
        try:
            we.fire_webhook_rule('gone', 'orders', 'rec-1', {}, 'admin', {}, 'main',
                                 action_id='ra-1', action_label='x')
        except ValueError as e:
            assert 'gone' in str(e)
        else:
            raise AssertionError('should raise ValueError')


def test_manual_rules_not_matched_by_data_events():
    """fire_webhooks 按 event 精确匹配，manual 规则不会被 create 触发。"""
    import utils.webhook_engine as we
    cur = MagicMock()
    cur.fetchall.return_value = []
    we.fire_webhooks('create', 'orders', 'rec-1', None, {'a': 1}, 'admin', cur=cur)
    sql = cur.execute.call_args[0][0]
    args = cur.execute.call_args[0][1]
    assert 'trigger_event = %s' in sql
    assert args[0] == 'create'
