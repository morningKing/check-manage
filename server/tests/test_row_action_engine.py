import os
import sys
from unittest.mock import patch

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


# ---------- 幂等闸门（webhook：原子 CAS 抢占） ----------

def test_webhook_claim_fails_row_is_rejected():
    """claim_for_webhook 抢不到（rowcount=0，行仍在执行中且未陈旧）→ 409，不分派。"""
    import utils.row_action_engine as eng
    with patch.object(eng, '_rule_stale_after_seconds', return_value=300), \
         patch.object(eng, 'claim_for_webhook', return_value=False) as claim, \
         patch.object(eng, '_spawn') as spawn:
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _action(), {'syncStatus': '同步中'},
                           'admin', 'admin', 'main', {})
    assert ei.value.http_status == 409
    claim.assert_called_once()
    spawn.assert_not_called()


def test_webhook_claim_succeeds_dispatches_and_returns_running():
    """claim_for_webhook 抢到（rowcount=1，包括陈旧放行的情形）→ 正常分派，返回 'running'。"""
    import utils.row_action_engine as eng
    with patch.object(eng, '_rule_stale_after_seconds', return_value=300), \
         patch.object(eng, 'claim_for_webhook', return_value=True) as claim, \
         patch.object(eng, '_spawn') as spawn:
        status = eng.run_action('orders', 'rec-1', _action(),
                                {'syncStatus': '同步中'}, 'admin', 'admin', 'main', {})
    assert status == 'running'
    claim.assert_called_once()
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
         patch.object(eng, 'write_back') as wb, \
         patch.object(eng, '_run_ai') as ra:
        status = eng.run_action('orders', 'rec-1', _ai_action(),
                                {'syncStatus': '同步中'}, 'admin', 'admin', 'main', {})
    assert status == 'running'
    ra.assert_called_once()
    # AI 分支的「执行中」由 claim_one 原子写入，run_action 不得自己写
    wb.assert_not_called()


def test_ai_action_gate_is_strict_no_stale_bypass():
    """AI 任务常跑超过 5 分钟，陈旧放行只对 webhook 生效——AI 闸门完全不看时间。"""
    import utils.row_action_engine as eng
    with patch.object(eng, 'get_task', return_value=AI_TASK), \
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
    with patch.object(eng, 'claim_for_webhook') as claim, \
         patch.object(eng, '_spawn') as spawn:
        status = eng.run_action('orders', 'rec-1', a, {}, 'admin', 'admin', 'main', {})
    assert status == 'submitted'
    claim.assert_not_called()
    spawn.assert_called_once()


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


def test_webhook_required_field_missing_in_json_writes_failed_value():
    """responseMapping 标了 required 的字段在响应 JSON 里缺失时判失败，不写映射字段。"""
    import utils.row_action_engine as eng
    a = _action(responseMapping=[{'jsonKey': 'code', 'column': 'extCode', 'required': True}])
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': '{"other": "x"}'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '同步失败'})


def test_webhook_required_field_empty_string_writes_failed_value():
    """required 字段存在但是空字符串，同样判失败。"""
    import utils.row_action_engine as eng
    a = _action(responseMapping=[{'jsonKey': 'code', 'column': 'extCode', 'required': True}])
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': '{"code": ""}'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '同步失败'})


def test_webhook_unparseable_response_with_required_mapping_writes_failed_value():
    """响应不是 JSON、且配了 required 映射时，required 自然满足不了，判失败（区别于无 required 时落 doneValue）。"""
    import utils.row_action_engine as eng
    a = _action(responseMapping=[{'jsonKey': 'code', 'column': 'extCode', 'required': True}])
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': '<html>ok</html>'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '同步失败'})


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


# ---------- I4：AI 分支异常兜底（对照 ai_scan_engine.run_task 的同款兜底） ----------

def test_ai_build_context_failure_reverts_claim_and_cleans_staging():
    """build_context_dir 抛错：行不能永久卡在"处理中"，暂存目录不能残留，
    异常不能是裸 500——必须还原状态 + 清目录 + 翻译成 RowActionError。"""
    import utils.row_action_engine as eng
    a = _ai_action()
    with patch.object(eng, 'claim_one', return_value={'id': 'rec-1', 'data': {}}), \
         patch.object(eng, 'build_context_dir', side_effect=OSError('disk full')), \
         patch.object(eng, '_revert_claimed') as revert, \
         patch.object(eng, 'shutil') as shutil_mock, \
         patch.object(eng, 'create_batch') as cb:
        with pytest.raises(eng.RowActionError) as ei:
            eng._run_ai('rec-1', a, AI_TASK, {})
    assert ei.value.http_status == 500
    revert.assert_called_once_with(AI_TASK, ['rec-1'])
    shutil_mock.rmtree.assert_called_once()
    cb.assert_not_called()


def test_ai_create_batch_failure_reverts_claim_and_cleans_staging():
    """create_batch（写库）抛错同样要兜底，而不仅仅是 build_context_dir。"""
    import utils.row_action_engine as eng
    a = _ai_action()
    with patch.object(eng, 'claim_one', return_value={'id': 'rec-1', 'data': {}}), \
         patch.object(eng, 'build_context_dir', return_value='scan-staging/st-1/rec-1'), \
         patch.object(eng, 'append_params_to_context'), \
         patch.object(eng, 'assemble_prompt', return_value='PROMPT'), \
         patch.object(eng, 'create_batch', side_effect=RuntimeError('db down')), \
         patch.object(eng, '_revert_claimed') as revert, \
         patch.object(eng, 'shutil') as shutil_mock:
        with pytest.raises(eng.RowActionError) as ei:
            eng._run_ai('rec-1', a, AI_TASK, {})
    assert ei.value.http_status == 500
    revert.assert_called_once_with(AI_TASK, ['rec-1'])
    shutil_mock.rmtree.assert_called_once()


# ---------- I5：AI 分支跨分支保护 ----------

def test_ai_action_cross_branch_blocked():
    """闸门读的是用户当前分支（branch_id='br-x'）的行，但 claim_one 实际认领、
    改写的是扫描任务绑定的 main 分支——两者不一致时必须挡住，不能静默改错行。"""
    import utils.row_action_engine as eng
    a = _ai_action()
    with patch.object(eng, 'get_task', return_value=AI_TASK), \
         patch.object(eng, '_run_ai') as ra:
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', a, {}, 'admin', 'admin', 'br-x', {})
    assert ei.value.http_status == 409
    assert '主分支' in ei.value.message
    ra.assert_not_called()


def test_ai_action_same_branch_not_blocked():
    import utils.row_action_engine as eng
    a = _ai_action()
    with patch.object(eng, 'get_task', return_value=AI_TASK), \
         patch.object(eng, '_run_ai') as ra:
        status = eng.run_action('orders', 'rec-1', a, {}, 'admin', 'admin', 'main', {})
    assert status == 'running'
    ra.assert_called_once()


# ---------- C1：成功值/失败值留空时不清空状态字段 ----------

def test_webhook_success_with_empty_done_value_skips_status_write():
    """doneValue 留空 = 成功时不动状态字段，而不是被 write_back 的
    None -> '' 兜底清空成空串。"""
    import utils.row_action_engine as eng
    a = _action(doneValue=None)
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': 'OK'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_not_called()


def test_webhook_failure_with_empty_failed_value_skips_status_write():
    import utils.row_action_engine as eng
    a = _action(failedValue=None)
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': False, 'responseBody': None}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_not_called()


def test_webhook_success_with_empty_done_value_still_writes_mapped_columns():
    """doneValue 留空但配了响应映射：映射字段照写，只是不动状态字段。"""
    import utils.row_action_engine as eng
    a = _action(doneValue=None,
               responseMapping=[{'jsonKey': 'code', 'column': 'extCode', 'required': False}])
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': '{"code": "X9"}'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'extCode': 'X9'})


def test_map_response_skips_explicit_null_optional_value():
    """非 required 的映射键在响应里显式是 null 时不写——同样不该被
    write_back 的 None -> '' 兜底清空目标字段的现有值。"""
    import utils.row_action_engine as eng
    a = _action(responseMapping=[{'jsonKey': 'code', 'column': 'extCode', 'required': False}])
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': '{"code": null}'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '已同步'})


# ---------- I7：webhook 分支用共享有界线程池，而不是每次点击起裸线程 ----------

def test_spawn_uses_shared_bounded_executor():
    import utils.row_action_engine as eng
    from concurrent.futures import ThreadPoolExecutor
    assert isinstance(eng._webhook_executor, ThreadPoolExecutor)
    assert eng._webhook_executor._max_workers == 5

    calls = []
    fut = eng._spawn(lambda x: calls.append(x), 'ping')
    fut.result(timeout=2)
    assert calls == ['ping']


# ---------- M1：run_action 与 is_visible 共用同一判断来源（消除逐字重写） ----------

def test_run_action_translates_shared_disabled_reason_to_400():
    import utils.row_action_engine as eng
    with patch.object(eng, '_visibility_denial_reason', return_value='disabled'):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _action(), {}, 'admin', 'admin', 'main', {})
    assert ei.value.http_status == 400


def test_run_action_translates_shared_role_reason_to_403():
    import utils.row_action_engine as eng
    with patch.object(eng, '_visibility_denial_reason', return_value='role'):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _action(), {}, 'admin', 'admin', 'main', {})
    assert ei.value.http_status == 403


def test_run_action_translates_shared_condition_reason_to_409():
    import utils.row_action_engine as eng
    with patch.object(eng, '_visibility_denial_reason', return_value='condition'):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _action(), {}, 'admin', 'admin', 'main', {})
    assert ei.value.http_status == 409


def test_is_visible_delegates_to_shared_reason_function():
    """锁住 is_visible 不再是死代码——它和 run_action 共读同一个函数，
    改坏这个函数两边都会红。"""
    import utils.row_action_engine as eng
    with patch.object(eng, '_visibility_denial_reason', return_value=None) as f:
        assert eng.is_visible(_action(), 'developer', False, {}) is True
    f.assert_called_once_with(_action(), 'developer', False, {})
