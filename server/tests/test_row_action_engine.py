import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _action(**over):
    a = {
        'id': 'ra-1', 'label': '推送外部', 'actionType': 'webhook',
        'enabled': True, 'webhookRuleId': 'wh-1',
        'statusField': 'syncStatus', 'runningValue': '同步中',
        'doneValue': '已同步', 'failedValue': '同步失败',
    }
    a.update(over)
    return a


# ---------- is_visible ----------

def test_visible_when_no_roles_and_no_condition():
    from utils.row_action_engine import is_visible
    assert is_visible(_action(), 'developer', False, {}) is True


def test_hidden_when_disabled():
    from utils.row_action_engine import is_visible
    assert is_visible(_action(enabled=False), 'developer', False, {}) is False


def test_hidden_when_role_not_whitelisted():
    from utils.row_action_engine import is_visible
    a = _action(roles=['admin'])
    assert is_visible(a, 'developer', False, {}) is False


def test_visible_when_role_whitelisted():
    from utils.row_action_engine import is_visible
    a = _action(roles=['developer', 'admin'])
    assert is_visible(a, 'developer', False, {}) is True


def test_superuser_bypasses_role_whitelist():
    from utils.row_action_engine import is_visible
    a = _action(roles=['nobody'])
    assert is_visible(a, 'admin', True, {}) is True


def test_hidden_when_condition_not_met():
    from utils.row_action_engine import is_visible
    a = _action(visibleWhen={'field': 'status', 'operator': 'eq', 'value': '待审核'})
    assert is_visible(a, 'developer', False, {'status': '已通过'}) is False


def test_visible_when_condition_met():
    from utils.row_action_engine import is_visible
    a = _action(visibleWhen={'field': 'status', 'operator': 'eq', 'value': '待审核'})
    assert is_visible(a, 'developer', False, {'status': '待审核'}) is True


# ---------- 幂等闸门 ----------

def test_running_row_is_rejected():
    import utils.row_action_engine as eng
    with patch.object(eng, 'write_status') as ws, \
         patch.object(eng, '_row_updated_at', return_value=datetime.now(timezone.utc)):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _action(), {'syncStatus': '同步中'},
                           'admin', 'admin', 'main', {})
    assert ei.value.http_status == 409
    ws.assert_not_called()


def test_stale_running_row_is_allowed_through():
    """webhook 动作：卡住超过阈值的「同步中」视为陈旧，允许重新触发。"""
    import utils.row_action_engine as eng
    stale = datetime.now(timezone.utc) - timedelta(minutes=30)
    with patch.object(eng, 'write_status'), \
         patch.object(eng, '_row_updated_at', return_value=stale), \
         patch.object(eng, '_rule_stale_after_seconds', return_value=300), \
         patch.object(eng, '_spawn') as spawn:
        status = eng.run_action('orders', 'rec-1', _action(),
                                {'syncStatus': '同步中'}, 'admin', 'admin', 'main', {})
    assert status == 'running'
    spawn.assert_called_once()


# ---------- AI 动作用扫描任务的状态配置 ----------

AI_TASK = {
    'id': 'st-1', 'collection': 'orders', 'branchId': 'main',
    'statusField': 'aiStatus', 'runningValue': '处理中', 'doneValue': '已处理',
    'failedValue': '处理失败', 'ownerUserId': 'u1', 'name': '审核',
    'promptTemplate': 'p', 'fieldMapping': [], 'agent': None,
}


def _ai_action(**over):
    a = {
        'id': 'ra-2', 'label': 'AI 审核', 'actionType': 'aiTask',
        'enabled': True, 'scanTaskId': 'st-1',
        # 这四项对 AI 动作应当被忽略
        'statusField': 'syncStatus', 'runningValue': '同步中',
        'doneValue': '已同步', 'failedValue': '同步失败',
    }
    a.update(over)
    return a


def test_ai_action_gate_uses_scan_task_status_not_action_status():
    """行动作自己的 statusField 对 AI 无效；闸门看扫描任务的 aiStatus。"""
    import utils.row_action_engine as eng
    with patch.object(eng, 'get_task', return_value=AI_TASK), \
         patch.object(eng, '_run_ai'):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _ai_action(),
                           {'aiStatus': '处理中'}, 'admin', 'admin', 'main', {})
    assert ei.value.http_status == 409


def test_ai_action_ignores_action_status_field_when_gating():
    """行动作的 syncStatus=同步中 不该拦住 AI 动作。"""
    import utils.row_action_engine as eng
    with patch.object(eng, 'get_task', return_value=AI_TASK), \
         patch.object(eng, 'write_status') as ws, \
         patch.object(eng, '_run_ai') as ra:
        status = eng.run_action('orders', 'rec-1', _ai_action(),
                                {'syncStatus': '同步中'}, 'admin', 'admin', 'main', {})
    assert status == 'running'
    ra.assert_called_once()
    # AI 分支的「执行中」由 claim_one 原子写入，run_action 不得自己写
    ws.assert_not_called()


def test_ai_action_gate_is_strict_no_stale_bypass():
    """AI 任务常跑超过 5 分钟，陈旧放行只对 webhook 生效。"""
    import utils.row_action_engine as eng
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    with patch.object(eng, 'get_task', return_value=AI_TASK), \
         patch.object(eng, '_row_updated_at', return_value=stale), \
         patch.object(eng, '_run_ai'):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _ai_action(),
                           {'aiStatus': '处理中'}, 'admin', 'admin', 'main', {})
    assert ei.value.http_status == 409


def test_ai_action_missing_task_raises_400_before_gate():
    import utils.row_action_engine as eng
    with patch.object(eng, 'get_task', return_value=None):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _ai_action(), {}, 'admin', 'admin',
                           'main', {})
    assert ei.value.http_status == 400


def test_no_status_field_means_no_gate_and_submitted():
    import utils.row_action_engine as eng
    a = _action(statusField=None, runningValue=None, doneValue=None, failedValue=None)
    with patch.object(eng, 'write_status') as ws, patch.object(eng, '_spawn'):
        status = eng.run_action('orders', 'rec-1', a, {}, 'admin', 'admin', 'main', {})
    assert status == 'submitted'
    ws.assert_not_called()


# ---------- 后端复核 ----------

def test_run_rejects_when_condition_not_met():
    import utils.row_action_engine as eng
    a = _action(visibleWhen={'field': 'status', 'operator': 'eq', 'value': '待审核'})
    with pytest.raises(eng.RowActionError) as ei:
        eng.run_action('orders', 'rec-1', a, {'status': '已通过'},
                       'developer', 'dev', 'main', {})
    assert ei.value.http_status == 409


def test_run_rejects_when_role_not_whitelisted():
    import utils.row_action_engine as eng
    a = _action(roles=['admin'])
    with pytest.raises(eng.RowActionError) as ei:
        eng.run_action('orders', 'rec-1', a, {}, 'developer', 'dev', 'main', {})
    assert ei.value.http_status == 403


# ---------- webhook 执行 ----------

def test_webhook_success_writes_done_and_mapped_columns():
    import utils.row_action_engine as eng
    a = _action(responseMapping=[{'jsonKey': 'code', 'column': 'extCode', 'required': False}])
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': '{"code": "X9"}'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main',
                               {'extCode': 'X9', 'syncStatus': '已同步'})


def test_webhook_success_without_mapping_writes_only_status():
    import utils.row_action_engine as eng
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': 'OK'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', _action(), {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '已同步'})


def test_webhook_failure_writes_failed_value():
    import utils.row_action_engine as eng
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': False, 'responseBody': None}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', _action(), {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '同步失败'})


def test_webhook_exception_writes_failed_value():
    import utils.row_action_engine as eng
    with patch.object(eng, 'fire_webhook_rule', side_effect=RuntimeError('boom')), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', _action(), {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '同步失败'})


def test_webhook_unparseable_response_still_writes_done():
    """响应不是 JSON 时不当作失败，只是没有可映射的值。"""
    import utils.row_action_engine as eng
    a = _action(responseMapping=[{'jsonKey': 'code', 'column': 'extCode', 'required': False}])
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': '<html>ok</html>'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '已同步'})


# ---------- AI 执行 ----------

def test_ai_claim_miss_raises_409():
    import utils.row_action_engine as eng
    a = _ai_action()
    with patch.object(eng, 'claim_one', return_value=None):
        with pytest.raises(eng.RowActionError) as ei:
            eng._run_ai('rec-1', a, AI_TASK, {})
    assert ei.value.http_status == 409


def test_ai_creates_single_child_batch():
    import utils.row_action_engine as eng
    a = _ai_action()
    task = dict(AI_TASK, agent='build')
    with patch.object(eng, 'claim_one', return_value={'id': 'rec-1', 'data': {}}), \
         patch.object(eng, 'build_context_dir', return_value='scan-staging/st-1/rec-1'), \
         patch.object(eng, 'assemble_prompt', return_value='PROMPT'), \
         patch.object(eng, 'append_params_to_context') as ap, \
         patch.object(eng, 'create_batch') as cb:
        eng._run_ai('rec-1', a, task, {'reason': '缺料'})
    ap.assert_called_once_with('scan-staging/st-1/rec-1', {'reason': '缺料'})
    kwargs = cb.call_args[1]
    assert kwargs['files'] == [{'name': 'rec-1', 'path': 'scan-staging/st-1/rec-1',
                                'recordId': 'rec-1'}]
    assert kwargs['scan_task_id'] == 'st-1'
    assert kwargs['agent'] == 'build'
