"""自定义行级操作按钮的执行编排。

本模块只做编排，不自己实现执行器：
  - webhook 分支 -> webhook_engine.fire_webhook_rule（HMAC/超时/重试/日志全复用）
  - AI 分支      -> ai_scan_engine.claim_one + build_context_dir + batch_repo.create_batch
                     （后续回写由 ai_scan_engine.on_child_finished 完成，不经过这里）

执行痕迹刻意不建表：webhook 的审计是 webhook_logs，AI 的审计是子会话完整对话。
行上的状态字段回答「现在怎么样」，那两处回答「当时发生了什么」。
"""

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import get_db
from utils.row_action_condition import evaluate
from utils.webhook_engine import fire_webhook_rule
from utils.ai_scan_engine import (
    claim_one, build_context_dir, assemble_prompt, _workspace_root,
)
from utils.ai_scan_repo import get_task
from utils.batch_repo import create_batch

logger = logging.getLogger(__name__)

# 卡住的 runningValue 超过这个秒数就视为陈旧，允许重新触发（进程崩溃兜底）
MIN_STALE_SECONDS = 300


class RowActionError(Exception):
    """带 HTTP 状态码的行动作错误，由路由层翻译成响应。"""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


# ==================== 可见性 ====================

def is_visible(action, role, is_superuser, row_data):
    """按钮对该角色 + 该行是否可见。前端与后端共用同一判断。"""
    if not action.get('enabled', True):
        return False
    roles = action.get('roles') or []
    if roles and not is_superuser and role not in roles:
        return False
    return evaluate(action.get('visibleWhen'), row_data or {})


# ==================== 行读写 ====================

def write_back(collection, record_id, branch_id, values):
    """把 {字段名: 值} 一次性写回该行（嵌套 jsonb_set，参数化）。"""
    if not values:
        return 0
    expr = 'data'
    params = []
    for col, val in values.items():
        expr = f'jsonb_set({expr}, ARRAY[%s], to_jsonb(%s::text))'
        params.extend([col, '' if val is None else str(val)])
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'UPDATE dynamic_data SET data = {expr}, updated_at = now(), '
                'version = version + 1 '
                'WHERE id = %s AND collection = %s AND branch_id = %s',
                params + [record_id, collection, branch_id],
            )
            n = cur.rowcount
        conn.commit()
    return n


def write_status(collection, record_id, branch_id, field, value):
    return write_back(collection, record_id, branch_id, {field: value})


# ==================== 陈旧阈值 ====================

def _rule_stale_after_seconds(action):
    """webhook 动作的陈旧阈值 = timeout × (retries + 1)，下限 MIN_STALE_SECONDS。

    AI 动作不走这条（它由 ai_scan_engine.sweep_orphans 兜底），直接返回下限。
    """
    if action.get('actionType') != 'webhook':
        return MIN_STALE_SECONDS
    rule_id = action.get('webhookRuleId')
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT timeout, retries FROM webhook_rules WHERE id = %s',
                        (rule_id,))
            row = cur.fetchone()
        if row:
            timeout, retries = (row[0] or 30), (row[1] or 0)
            return max(MIN_STALE_SECONDS, timeout * (retries + 1))
    except Exception:
        logger.exception('读取 webhook 规则超时配置失败: %s', rule_id)
    return MIN_STALE_SECONDS


# ==================== webhook 幂等闸门（原子抢占） ====================

def claim_for_webhook(collection, record_id, branch_id, field, running, stale_before):
    """原子地把该行置为「执行中」，抢到返回 True，没抢到返回 False。

    条件 UPDATE（CAS）而不是「读后写」：两个并发请求各自拿到「未在执行中」的
    行快照时，读后写会双双通过闸门、双双 `_spawn` 重复触发外部 webhook。这里
    把判断和写入压进同一条 UPDATE，由 Postgres 的行级锁保证并发下只有一个
    请求能真正改到行——第二个请求的 UPDATE 会等第一个提交后再重新求值 WHERE
    条件，此时该行已经是 running，条件不成立，rowcount = 0。

    stale_before: 早于这个时刻的 updated_at 视为陈旧（进程崩溃留下的孤儿
    行），允许重新抢占。传 None 表示不启用陈旧放行。
    """
    cond_sql = 'data->>%s IS DISTINCT FROM %s'
    params = [field, running, record_id, collection, branch_id, field, running]
    if stale_before is not None:
        cond_sql = f'({cond_sql}) OR updated_at < %s'
        params.append(stale_before)
    sql = (
        'UPDATE dynamic_data '
        'SET data = jsonb_set(data, ARRAY[%s], to_jsonb(%s::text)), '
        '    updated_at = now(), version = version + 1 '
        'WHERE id = %s AND collection = %s AND branch_id = %s '
        f'AND ({cond_sql})'
    )
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            n = cur.rowcount
        conn.commit()
    return n > 0


# ==================== 主入口 ====================

def _spawn(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t


def run_action(collection, record_id, action, row_data, role, operator,
               branch_id, params):
    """校验 → 写「执行中」→ 分派。返回 'running' 或 'submitted'。

    状态字段的归属：
      - webhook 动作用行动作自己配置的 statusField / 三个状态值
      - AI 动作**一律**用所绑扫描任务的配置。终态回写由
        ai_scan_engine.on_child_finished 完成，它只认扫描任务的配置；行动作
        再叠一套只会在行上留下一个永远卡在「执行中」、无人写终态的字段。

    Raises: RowActionError
    """
    # 1. 后端复核（前端隐藏不算数）
    if not action.get('enabled', True):
        raise RowActionError('该行操作已停用', 400)
    roles = action.get('roles') or []
    is_superuser = (role == 'admin')
    if roles and not is_superuser and role not in roles:
        raise RowActionError('权限不足', 403)
    if not evaluate(action.get('visibleWhen'), row_data or {}):
        raise RowActionError('当前记录不满足该动作的执行条件', 409)

    is_ai = action.get('actionType') == 'aiTask'
    task = None
    if is_ai:
        task = get_task(action.get('scanTaskId'))
        if not task:
            raise RowActionError('动作绑定的执行器已不存在', 400)
        status_field = task.get('statusField')
        running = task.get('runningValue')
    else:
        status_field = action.get('statusField')
        running = action.get('runningValue')

    # 2. 幂等闸门
    #    webhook：条件 UPDATE（CAS）原子抢占，避免并发「读后写」重复触发外部
    #        webhook；陈旧的 runningValue（进程崩溃孤儿）放行重新抢占。
    #    AI：严格拦截，不看时间 —— AI 常跑超过 5 分钟，按时间放行会给同一条
    #        记录并发出第二个子会话；孤儿由 ai_scan_engine.sweep_orphans 恢复。
    #        AI 分支的「执行中」由 claim_one 原子写入，这里不能抢先写，否则
    #        claim_one 之前的窗口里状态已变但记录尚未真正认领。
    if status_field and running:
        if is_ai:
            current = (row_data or {}).get(status_field)
            if current == running:
                raise RowActionError('该行有正在执行的动作，请稍后再试', 409)
        else:
            stale_before = (datetime.now(timezone.utc)
                            - timedelta(seconds=_rule_stale_after_seconds(action)))
            if not claim_for_webhook(collection, record_id, branch_id,
                                     status_field, running, stale_before):
                raise RowActionError('该行有正在执行的动作，请稍后再试', 409)

    # 3. 分派
    if is_ai:
        # 同步执行到「创建批任务」为止（很快），失败要能立刻告诉用户
        _run_ai(record_id, action, task, params)
    else:
        _spawn(_run_webhook, collection, record_id, action, row_data,
               operator, branch_id, params)

    return 'running' if (status_field and running) else 'submitted'


# ==================== webhook 分支 ====================

def _run_webhook(collection, record_id, action, row_data, operator, branch_id, params):
    """后台线程执行。finally 保证落一个终态。"""
    status_field = action.get('statusField')
    values = {}
    ok = False
    try:
        res = fire_webhook_rule(
            action['webhookRuleId'], collection, record_id, row_data or {},
            operator, params or {}, branch_id,
            action_id=action['id'], action_label=action.get('label') or '',
        )
        ok = bool(res.get('success'))
        if ok:
            mapped, mapping_ok = _map_response(res.get('responseBody'),
                                               action.get('responseMapping') or [])
            ok = mapping_ok
            if mapping_ok:
                values.update(mapped)
    except Exception:
        logger.exception('行操作 webhook 执行失败: action=%s record=%s',
                         action.get('id'), record_id)
        ok = False
    finally:
        if status_field:
            values[status_field] = (action.get('doneValue') if ok
                                    else action.get('failedValue'))
        if values:
            try:
                write_back(collection, record_id, branch_id, values)
            except Exception:
                logger.exception('行操作结果回写失败: record=%s', record_id)


def _map_response(body, mapping):
    """按 responseMapping 从响应 JSON 取值。

    返回 (values, ok)：
      - 响应体解析不出 JSON 对象时，没有 required 映射就不算失败（只是没有
        可映射的值——对方返回 HTML 也要落 doneValue）；配了 required 映射时
        解析不出 JSON 自然满足不了 required，判失败。
      - 解析出 JSON 后，required 的 jsonKey 缺失或为 None/''（对齐
        ai_scan_engine.on_child_finished 对 required 的判断）时判失败，且
        不返回任何映射值——调用方据此只落 failedValue，不写半截数据。
    """
    required = [m.get('jsonKey') for m in mapping if m.get('required')]
    parsed = None
    if body:
        try:
            candidate = json.loads(body)
        except (ValueError, TypeError):
            candidate = None
        if isinstance(candidate, dict):
            parsed = candidate
    if parsed is None:
        return {}, not required
    if any(parsed.get(k) in (None, '') for k in required):
        return {}, False
    out = {}
    for m in mapping:
        key, col = m.get('jsonKey'), m.get('column')
        if key and col and key in parsed:
            out[col] = parsed[key]
    return out, True


# ==================== AI 分支 ====================

def append_params_to_context(rel_path, params):
    """把参数表单的值以「## 本次执行参数」段落追加进 record.md。

    prompt 是 batch 级共享的，params 是单行的；写进上下文文件语义更正确，
    而且 assemble_prompt 不用动。
    """
    if not params:
        return
    md = Path(_workspace_root()) / rel_path / 'record.md'
    if not md.exists():
        return
    lines = ['', '## 本次执行参数', '']
    for k, v in params.items():
        lines.append(f'- {k}: {v}')
    with md.open('a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def _run_ai(record_id, action, task, params):
    """认领该行 → 铺上下文目录 → 建单子会话批任务。

    `task` 由 run_action 预先取好（闸门也要用它的状态配置），不在这里重复取。
    collection / branch_id 都以 task 里的为准 —— 扫描任务本来就绑定了集合与分支。
    """
    rec = claim_one(task, record_id)
    if not rec:
        raise RowActionError('记录不存在或正被其他操作占用', 409)

    rel = build_context_dir(task, rec)
    append_params_to_context(rel, params)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    create_batch(
        task['ownerUserId'],
        name=f"行操作·{action.get('label')}·{stamp}",
        prompt=assemble_prompt(task),
        template_id=None,
        files=[{'name': record_id, 'path': rel, 'recordId': record_id}],
        scan_task_id=task['id'],
        agent=task.get('agent') or None,
    )
