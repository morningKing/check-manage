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
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import get_db
from utils.row_action_condition import evaluate
from utils.webhook_engine import fire_webhook_rule
from utils.ai_scan_engine import (
    claim_one, build_context_dir, assemble_prompt, _workspace_root, _revert_claimed,
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

def _visibility_denial_reason(action, role, is_superuser, row_data):
    """是 is_visible 和 run_action 共用的唯一判断来源。

    返回 None 表示可见/可执行；否则是被拒绝的原因：'disabled' | 'role' | 'condition'。
    此前 run_action 把这三条判断逐字重写了一遍，导致 is_visible 的测试是假信心——
    改坏 is_visible 不影响任何运行路径。两者现在共读这一个函数，run_action 借
    reason 选择对应的 HTTP 状态码/中文提示，is_visible 只关心"能不能"。
    """
    if not action.get('enabled', True):
        return 'disabled'
    roles = action.get('roles') or []
    if roles and not is_superuser and role not in roles:
        return 'role'
    if not evaluate(action.get('visibleWhen'), row_data or {}):
        return 'condition'
    return None


def is_visible(action, role, is_superuser, row_data):
    """按钮对该角色 + 该行是否可见。前端与后端共用同一判断语义。"""
    return _visibility_denial_reason(action, role, is_superuser, row_data) is None


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

# 共享的小线程池：webhook 分支的执行是"点一次按钮起一个后台任务"，此前用裸
# `threading.Thread(daemon=True)`，一个有权限的用户脚本循环点 1000 次就是
# 1000 个线程 + 1000 次对外 HTTP（每次还带重试）。改成模块级共享的小池子
# （webhook 请求本身有超时+重试上限，不会无限占用）。用真正的 Executor 而不是
# daemon 线程还有个好处：进程退出时 concurrent.futures 自带的 atexit 钩子会
# 等待在跑的任务收尾，不会像 daemon 线程那样被进程退出直接拦腰砍断、留下半
# 写的 webhook 结果。
_webhook_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix='row-action-webhook')


def _spawn(fn, *args):
    return _webhook_executor.submit(fn, *args)


def _branch_display_name(branch_id):
    """把 branch_id 转成人话，用于 I5 的跨分支保护提示。"""
    if not branch_id or branch_id == 'main':
        return '主分支'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT name FROM collection_versions WHERE id = %s', (branch_id,))
            row = cur.fetchone()
        return row[0] if row else branch_id
    except Exception:
        logger.exception('查询分支名称失败: %s', branch_id)
        return branch_id


def resolve_status_gate(action):
    """解析该动作实际用于闸门/轮询的 (status_field, running_value, task, is_ai)。

    webhook 用行动作自己的配置；AI **一律**用所绑扫描任务的配置（终态回写由
    ai_scan_engine.on_child_finished 完成，它只认扫描任务的配置——行动作自己
    再叠一套状态字段只会在行上留下一个永远卡在"执行中"、无人写终态的字段）。

    run_action 和路由层（组装 /run 响应，供前端轮询判断终态）共读这一个函数，
    避免"AI 用扫描任务的状态配置"这条规则被两处分别实现、后续改漏一处。

    Raises: RowActionError（actionType='aiTask' 但绑定的扫描任务已被删除）
    """
    if action.get('actionType') == 'aiTask':
        task = get_task(action.get('scanTaskId'))
        if not task:
            raise RowActionError('动作绑定的执行器已不存在', 400)
        return task.get('statusField'), task.get('runningValue'), task, True
    return action.get('statusField'), action.get('runningValue'), None, False


def run_action(collection, record_id, action, row_data, role, operator,
               branch_id, params):
    """校验 → 写「执行中」→ 分派。返回 'running' 或 'submitted'。

    Raises: RowActionError
    """
    # 1. 后端复核（前端隐藏不算数）
    is_superuser = (role == 'admin')
    reason = _visibility_denial_reason(action, role, is_superuser, row_data)
    if reason == 'disabled':
        raise RowActionError('该行操作已停用', 400)
    if reason == 'role':
        raise RowActionError('权限不足', 403)
    if reason == 'condition':
        raise RowActionError('当前记录不满足该动作的执行条件', 409)

    status_field, running, task, is_ai = resolve_status_gate(action)

    # 1.5 AI 分支的跨分支保护：路由层的闸门读的是用户当前分支的这一行（同一
    #     record_id 在分支复制后每个分支各有一份），但 claim_one 实际认领、
    #     读写的是扫描任务绑定的 task['branchId']。两者不一致时，静默地按
    #     用户当前分支的按钮改了 main（或别的分支）上那条他没在看的记录——
    #     比直接报错更危险，必须在分派前挡住。
    if is_ai:
        task_branch = task.get('branchId') or 'main'
        if task_branch != branch_id:
            raise RowActionError(
                f'该动作绑定的 AI 任务作用于「{_branch_display_name(task_branch)}」分支，'
                '请切换分支后再执行', 409)

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
            # C1 修复：doneValue/failedValue 留空是合法配置（管理员想表达"成功
            # 就别动这个字段"），但 write_back 对 None 值的兜底是写成 ''——
            # 两者叠加会把行上一个正常业务字段（比如复用已有的 status 字段）
            # 静默清空成空串。留空就不写这个字段，维持原值不变。
            target = action.get('doneValue') if ok else action.get('failedValue')
            if target:
                values[status_field] = target
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
        # C1 修复：非 required 的映射键如果在响应里显式是 null，同样不该写——
        # 走到 write_back 会被它的 None -> '' 兜底覆盖掉那个字段的现有值，
        # 跟 required 缺失是同一类"映射值缺失就不该写那个字段"的问题。
        if key and col and key in parsed and parsed[key] is not None:
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

    try:
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
    except Exception as e:
        # I4 修复：claim_one 已经把这一行翻成"处理中"；build_context_dir（文件
        # IO、拷附件）/ create_batch（写库）任一步再抛错，此前完全没有兜底——
        # 行永久卡在"处理中"、scan-staging/<task>/<record>/ 暂存目录残留、
        # 异常一路冒到路由层变成裸 500（前端只看到通用错误提示）。对照
        # ai_scan_engine.run_task 的同款兜底：还原行状态 + 清理暂存目录 +
        # 抛 RowActionError 让路由层翻译成中文错误。
        _revert_claimed(task, [record_id])
        shutil.rmtree(
            Path(_workspace_root()) / 'scan-staging' / task['id'] / record_id,
            ignore_errors=True,
        )
        logger.exception('行操作 AI 分支执行失败: action=%s record=%s',
                         action.get('id'), record_id)
        raise RowActionError(f'AI 执行器启动失败：{e}', 500)
