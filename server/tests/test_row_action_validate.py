import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

FIELDS = [
    {'fieldName': 'status', 'label': '状态', 'controlType': 'select'},
    {'fieldName': 'result', 'label': '结果', 'controlType': 'text'},
    {'fieldName': 'tags', 'label': '标签', 'controlType': 'multiSelect'},
]

COLLECTION = 'orders'


def _cur(webhook_rules=None, scan_tasks=None):
    """mock cursor：按 SQL 里出现的表名决定 fetchone 返回值。

    webhook_rules: {rule_id: trigger_event}
    scan_tasks: {task_id: collection}
    """
    webhook_rules = webhook_rules or {}
    scan_tasks = scan_tasks or {}
    cur = MagicMock()

    def execute(sql, params=None):
        cur._last = (sql, params)

    def fetchone():
        sql, params = cur._last
        target = params[0]
        if 'webhook_rules' in sql:
            return (webhook_rules[target],) if target in webhook_rules else None
        if 'ai_scan_tasks' in sql:
            return (scan_tasks[target],) if target in scan_tasks else None
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
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    assert validate_row_actions([_base()], FIELDS, cur, COLLECTION) is None


def test_valid_ai_action_passes():
    from utils.row_action_validate import validate_row_actions
    a = _base(actionType='aiTask', webhookRuleId=None, scanTaskId='st-1')
    cur = _cur(scan_tasks={'st-1': COLLECTION})
    assert validate_row_actions([a], FIELDS, cur, COLLECTION) is None


def test_empty_list_passes():
    from utils.row_action_validate import validate_row_actions
    assert validate_row_actions([], FIELDS, _cur(), COLLECTION) is None


def test_missing_label_rejected():
    from utils.row_action_validate import validate_row_actions
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([_base(label='')], FIELDS, cur, COLLECTION)
    assert err and '名称' in err


def test_duplicate_id_rejected():
    from utils.row_action_validate import validate_row_actions
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([_base(), _base()], FIELDS, cur, COLLECTION)
    assert err and '唯一' in err


def test_unknown_action_type_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base(actionType='sms')], FIELDS, _cur(), COLLECTION)
    assert err and '类型' in err


def test_missing_webhook_rule_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base()], FIELDS, _cur(), COLLECTION)
    assert err and 'Webhook' in err


def test_missing_scan_task_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(actionType='aiTask', webhookRuleId=None, scanTaskId='nope')
    cur = _cur(scan_tasks={'st-1': COLLECTION})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and '扫描任务' in err


def test_unknown_status_field_rejected():
    from utils.row_action_validate import validate_row_actions
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([_base(statusField='ghost')], FIELDS, cur, COLLECTION)
    assert err and 'ghost' in err


def test_unknown_visible_when_field_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(visibleWhen={'field': 'ghost', 'operator': 'eq', 'value': 'x'})
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and 'ghost' in err


def test_unknown_response_mapping_column_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(responseMapping=[{'jsonKey': 'k', 'column': 'ghost', 'required': False}])
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and 'ghost' in err


def test_known_fields_accepted():
    from utils.row_action_validate import validate_row_actions
    a = _base(
        statusField='status', runningValue='执行中', doneValue='已完成', failedValue='失败',
        visibleWhen={'field': 'status', 'operator': 'eq', 'value': '待审核'},
        responseMapping=[{'jsonKey': 'k', 'column': 'result', 'required': True}],
    )
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    assert validate_row_actions([a], FIELDS, cur, COLLECTION) is None


def test_relation_param_field_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(paramFields=[{'fieldName': 'p', 'label': '关联', 'controlType': 'relation'}])
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and 'relation' in err


def test_text_param_field_accepted():
    from utils.row_action_validate import validate_row_actions
    a = _base(paramFields=[{'fieldName': 'p', 'label': '原因', 'controlType': 'textarea'}])
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    assert validate_row_actions([a], FIELDS, cur, COLLECTION) is None


# ---------- C1：状态字段配了却缺执行中值/成功值/失败值 ----------

def test_status_field_without_running_value_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(statusField='status', doneValue='已完成', failedValue='失败')
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and '执行中值' in err


def test_status_field_without_done_value_rejected():
    """回归 C1：成功值留空曾经会让引擎把状态字段静默清成空串。"""
    from utils.row_action_validate import validate_row_actions
    a = _base(statusField='status', runningValue='执行中', failedValue='失败')
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and '成功值' in err


def test_status_field_without_failed_value_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(statusField='status', runningValue='执行中', doneValue='已完成')
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and '失败值' in err


def test_no_status_field_does_not_require_the_three_values():
    from utils.row_action_validate import validate_row_actions
    a = _base()
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    assert validate_row_actions([a], FIELDS, cur, COLLECTION) is None


# ---------- I1：非标量字段不能用作状态字段/显示条件/响应映射目标 ----------

def test_non_scalar_status_field_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(statusField='tags', runningValue='x', doneValue='y', failedValue='z')
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and 'tags' in err and 'multiSelect' in err


def test_non_scalar_visible_when_field_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(visibleWhen={'field': 'tags', 'operator': 'empty'})
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and 'tags' in err


def test_non_scalar_response_mapping_column_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(responseMapping=[{'jsonKey': 'k', 'column': 'tags', 'required': False}])
    cur = _cur(webhook_rules={'wh-1': 'manual'})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and 'tags' in err


# ---------- I6：AI 扫描任务 collection 必须与本页面一致 ----------

def test_scan_task_collection_mismatch_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(actionType='aiTask', webhookRuleId=None, scanTaskId='st-1')
    cur = _cur(scan_tasks={'st-1': 'customers'})
    err = validate_row_actions([a], FIELDS, cur, COLLECTION)
    assert err and 'customers' in err and COLLECTION in err


def test_scan_task_collection_check_skipped_when_collection_not_given():
    """collection=None（旧调用点/未传）时跳过这条校验，不误报。"""
    from utils.row_action_validate import validate_row_actions
    a = _base(actionType='aiTask', webhookRuleId=None, scanTaskId='st-1')
    cur = _cur(scan_tasks={'st-1': 'customers'})
    assert validate_row_actions([a], FIELDS, cur, None) is None


# ---------- I6：Webhook 规则必须 triggerEvent='manual' ----------

def test_webhook_rule_non_manual_trigger_rejected():
    from utils.row_action_validate import validate_row_actions
    cur = _cur(webhook_rules={'wh-1': 'create'})
    err = validate_row_actions([_base()], FIELDS, cur, COLLECTION)
    assert err and '手动' in err
