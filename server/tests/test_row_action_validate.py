import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

FIELDS = [
    {'fieldName': 'status', 'label': '状态', 'controlType': 'select'},
    {'fieldName': 'result', 'label': '结果', 'controlType': 'text'},
]


def _cur(webhook_ids=(), scan_ids=()):
    """mock cursor：按 SQL 里出现的表名决定 fetchone 返回值。"""
    cur = MagicMock()

    def execute(sql, params=None):
        cur._last = (sql, params)

    def fetchone():
        sql, params = cur._last
        target = params[0]
        if 'webhook_rules' in sql:
            return (target,) if target in webhook_ids else None
        if 'ai_scan_tasks' in sql:
            return (target,) if target in scan_ids else None
        return None

    cur.execute.side_effect = execute
    cur.fetchone.side_effect = fetchone
    return cur


def _base(**over):
    a = {
        'id': 'ra-1', 'label': '推送外部', 'actionType': 'webhook',
        'enabled': True, 'webhookRuleId': 'wh-1',
    }
    a.update(over)
    return a


def test_valid_webhook_action_passes():
    from utils.row_action_validate import validate_row_actions
    assert validate_row_actions([_base()], FIELDS, _cur(webhook_ids=('wh-1',))) is None


def test_valid_ai_action_passes():
    from utils.row_action_validate import validate_row_actions
    a = _base(actionType='aiTask', webhookRuleId=None, scanTaskId='st-1')
    assert validate_row_actions([a], FIELDS, _cur(scan_ids=('st-1',))) is None


def test_empty_list_passes():
    from utils.row_action_validate import validate_row_actions
    assert validate_row_actions([], FIELDS, _cur()) is None


def test_missing_label_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base(label='')], FIELDS, _cur(webhook_ids=('wh-1',)))
    assert err and '名称' in err


def test_duplicate_id_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base(), _base()], FIELDS, _cur(webhook_ids=('wh-1',)))
    assert err and '唯一' in err


def test_unknown_action_type_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base(actionType='sms')], FIELDS, _cur())
    assert err and '类型' in err


def test_missing_webhook_rule_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base()], FIELDS, _cur(webhook_ids=()))
    assert err and 'Webhook' in err


def test_missing_scan_task_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(actionType='aiTask', webhookRuleId=None, scanTaskId='nope')
    err = validate_row_actions([a], FIELDS, _cur(scan_ids=('st-1',)))
    assert err and '扫描任务' in err


def test_unknown_status_field_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base(statusField='ghost')], FIELDS,
                               _cur(webhook_ids=('wh-1',)))
    assert err and 'ghost' in err


def test_unknown_visible_when_field_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(visibleWhen={'field': 'ghost', 'operator': 'eq', 'value': 'x'})
    err = validate_row_actions([a], FIELDS, _cur(webhook_ids=('wh-1',)))
    assert err and 'ghost' in err


def test_unknown_response_mapping_column_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(responseMapping=[{'jsonKey': 'k', 'column': 'ghost', 'required': False}])
    err = validate_row_actions([a], FIELDS, _cur(webhook_ids=('wh-1',)))
    assert err and 'ghost' in err


def test_known_fields_accepted():
    from utils.row_action_validate import validate_row_actions
    a = _base(
        statusField='status',
        visibleWhen={'field': 'status', 'operator': 'eq', 'value': '待审核'},
        responseMapping=[{'jsonKey': 'k', 'column': 'result', 'required': True}],
    )
    assert validate_row_actions([a], FIELDS, _cur(webhook_ids=('wh-1',))) is None


def test_relation_param_field_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(paramFields=[{'fieldName': 'p', 'label': '关联', 'controlType': 'relation'}])
    err = validate_row_actions([a], FIELDS, _cur(webhook_ids=('wh-1',)))
    assert err and 'relation' in err


def test_text_param_field_accepted():
    from utils.row_action_validate import validate_row_actions
    a = _base(paramFields=[{'fieldName': 'p', 'label': '原因', 'controlType': 'textarea'}])
    assert validate_row_actions([a], FIELDS, _cur(webhook_ids=('wh-1',))) is None
