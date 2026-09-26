"""AI 编排引擎（ai-harness-p2 spec §6/§11 Phase A-C 核心）。

Run/Step 三层模型：
- 状态派生自事实：run 状态由 step 事实派生；step 状态由子会话（执行事实）
  派生；模型的 Todo 永远不能推进 DAG（沿用 todo_trace 的 declared_plan 边界）。
- agent step 的执行体 = ai_chat_sessions 子会话（orchestration_run_id/
  orchestration_step_id 关联），由批 worker 认领驱动——复用 P0 的
  ownership/CAS/generation 与 P1 的 lease/checkpoint 全部安全语义。
- join/approval 是结构性节点，由本引擎的 scheduler 推进，不占执行槽。

调度器持 `lease_kind='scheduler'` 租约（多进程单实例）。
"""
import json
import logging
import os
import secrets
import threading
import time

logger = logging.getLogger(__name__)

RUN_ACTIVE = ('pending', 'running', 'waiting_approval', 'recovering')
STEP_TERMINAL = ('succeeded', 'failed', 'skipped', 'needs_review')


# ---------------------------------------------------------------------------
# Run 创建（冻结定义版本 + 输入快照）
# ---------------------------------------------------------------------------

def create_run(def_id: str, requested_by: str, *,
               run_input: dict | None = None,
               requested_by_kind: str = 'user') -> dict:
    from utils import orchestration_defs
    d = orchestration_defs.get_definition(def_id)
    if not d:
        raise ValueError('definition not found')
    run_id = 'run_' + secrets.token_hex(7)
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_orchestration_runs "
                "  (id, definition_id, definition_version, status, "
                "   run_input_snapshot, requested_by, requested_by_kind) "
                "VALUES (%s, %s, %s, 'pending', %s::jsonb, %s, %s)",
                (run_id, def_id, d['version'],
                 json.dumps(run_input or {}, ensure_ascii=False),
                 requested_by, requested_by_kind))
            # 展开节点为 step 行；depends_on 由入边推导
            for n in d['nodes']:
                deps = sorted({e['source'] for e in d['edges']
                               if e['target'] == n['id']})
                cur.execute(
                    "INSERT INTO ai_orchestration_steps "
                    "  (id, run_id, node_id, kind, name, status, depends_on, node_def) "
                    "VALUES (%s, %s, %s, %s, %s, 'blocked', %s::jsonb, %s::jsonb)",
                    (f'{run_id}:{n["id"]}', run_id, n['id'], n['kind'],
                     n.get('name') or n['id'],
                     json.dumps(deps),
                     json.dumps(n, ensure_ascii=False)))
        conn.commit()
    from utils import batch_events
    batch_events.append_event(run_id, 'run.created', aggregate_type='run',
                              aggregate_id=run_id,
                              payload={'definitionId': def_id,
                                       'definitionVersion': d['version']})
    return get_run(run_id)


def get_run(run_id: str) -> dict | None:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, definition_id, definition_version, status, "
                "run_input_snapshot, requested_by, error_code, error_message, "
                "started_at, finished_at, created_at "
                "FROM ai_orchestration_runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [dd[0] for dd in cur.description]
            run = dict(zip(cols, row))
            cur.execute(
                "SELECT id, node_id, kind, name, status, depends_on, "
                "node_def, session_id, attempt_count, output, error_message, "
                "started_at, finished_at "
                "FROM ai_orchestration_steps WHERE run_id = %s "
                "ORDER BY id", (run_id,))
            scols = [dd[0] for dd in cur.description]
            steps = [dict(zip(scols, r)) for r in cur.fetchall()]
    for r in (*[run], *steps):
        for k in ('started_at', 'finished_at', 'created_at'):
            if r.get(k) is not None:
                r[k] = r[k].isoformat()
    run['steps'] = steps
    return run


def list_runs(limit: int = 50) -> list[dict]:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, definition_id, definition_version, status, "
                "requested_by, error_code, created_at "
                "FROM ai_orchestration_runs ORDER BY created_at DESC LIMIT %s",
                (limit,))
            cols = [dd[0] for dd in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        if r.get('created_at') is not None:
            r['createdAt'] = r.pop('created_at').isoformat()
    return rows


# ---------------------------------------------------------------------------
# DAG 推进（scheduler tick）
# ---------------------------------------------------------------------------

def _edge_matches(edge, source_step: dict) -> bool:
    """条件边判定：source 输出 JSON 的 field 与 op/value 比较。
    无 condition 的边恒命中（默认边）。field='text' 读 output['text']
    （H5 残留修复：生产路径 output 形状即 {'text': …}）。"""
    cond = edge.get('condition')
    if not cond:
        return True
    out = source_step.get('output') or {}
    if not isinstance(out, dict):
        return False
    if cond.get('field') == 'text':
        actual = out.get('text')
        op = cond.get('op', 'contains')
        value = cond.get('value')
        if op == 'contains':
            return actual is not None and str(value) in str(actual)
        if op == 'not_contains':
            return actual is None or str(value) not in str(actual)
        if op == '==':
            return actual == value
        return False
    actual = out.get(cond.get('field'))
    op = cond.get('op', '==')
    value = cond.get('value')
    try:
        if op == '==':
            return actual == value
        if op == '!=':
            return actual != value
        if op == '>':
            return actual is not None and float(actual) > float(value)
        if op == '<':
            return actual is not None and float(actual) < float(value)
        if op == '>=':
            return actual is not None and float(actual) >= float(value)
        if op == '<=':
            return actual is not None and float(actual) <= float(value)
    except (TypeError, ValueError):
        return False
    return False


def _render_prompt(template: str, run: dict, steps_by_node: dict) -> str:
    """最小模板渲染：{{input.x}} / {{steps.<node_id>}}（上一步输出文本）。"""
    out = template
    for key, val in (run.get('run_input_snapshot') or {}).items():
        out = out.replace('{{input.' + key + '}}',
                          val if isinstance(val, str) else json.dumps(val, ensure_ascii=False))
    out = out.replace('{{input}}',
                      json.dumps(run.get('run_input_snapshot') or {},
                                 ensure_ascii=False))
    for node_id, st in steps_by_node.items():
        out_text = ''
        o = st.get('output')
        if isinstance(o, dict):
            out_text = o.get('text') or json.dumps(o, ensure_ascii=False)
        elif isinstance(o, str):
            out_text = o
        out = out.replace('{{steps.' + node_id + '}}', out_text)
    return out


def _advance_run(run_id: str) -> None:
    """scheduler 的一步：可达性计算 → runnable 推进 → run 状态派生。
    由持租约的调度线程串行调用；Approval/Join 在这里落地。

    H4 修复说明：并发安全不依赖全局锁（advisory lock 在客户端崩溃时会以
    idle-in-transaction 形式毒化后续所有推进），而是靠每处 step 转移的
    状态谓词 CAS——重复推进时输者 0 行生效，效果等价且无死锁面。"""
    return _advance_run_locked(run_id)


def _advance_run_locked(run_id: str) -> None:
    from db import get_db
    from utils import batch_events
    run = get_run(run_id)
    if not run or run['status'] not in RUN_ACTIVE:
        return
    steps = run['steps']
    by_node = {s['node_id']: s for s in steps}
    edges = _run_edges(run)
    nodes_def = {s['node_id']: s for s in steps}

    # 1) 可达性：从无入边的根出发，沿"生效边"走；不可达 step → skipped。
    # 生效边语义（借鉴 workflow_engine._advance_targets）：某节点的出边里
    # 有命中条件的条件边 → 只取命中的那些（并行扇出）；全不命中 → 取全部
    # 默认边（无 condition）。
    incoming = {}
    for e in edges:
        incoming.setdefault(e['target'], []).append(e)

    def _active_targets(src: str) -> list[str]:
        out_edges = [e for e in edges if e['source'] == src]
        conditional = [e for e in out_edges if e.get('condition')]
        if conditional:
            src_step = by_node.get(src) or {}
            # H5 残留修复：源节点尚未收敛（无 output 可读）时不做条件取舍——
            # 把全部出边目标都视为候选。此前"无 output → 条件全不命中 →
            # 回落默认边"会让条件目标在本轮被不可逆 skip，源节点收敛后
            # 也永不可达。真正的分支取舍推迟到源节点 succeeded。
            if src_step.get('status') != 'succeeded':
                return [e['target'] for e in out_edges]
            hits = [e for e in conditional if _edge_matches(e, src_step)]
            return [e['target'] for e in (hits if hits else
                                          [e for e in out_edges
                                           if not e.get('condition')])]
        return [e['target'] for e in out_edges]

    reached = set()
    frontier = [n for n in by_node if not incoming.get(n)]
    while frontier:
        cur_node = frontier.pop()
        if cur_node in reached:
            continue
        reached.add(cur_node)
        frontier.extend(t for t in _active_targets(cur_node) if t not in reached)
    changed = False
    with get_db() as conn:
        with conn.cursor() as cur:
            for s in steps:
                if s['status'] != 'blocked':
                    continue
                # 不可达（分支未命中）、依赖已失败/被跳过（失败与跳过均向
                # 下游传播，H6：否则菱形 DAG 一侧 skipped 会让 join 永久
                # blocked、run 卡 running）→ skipped
                deps = by_node  # node_id -> step
                dep_dead = any(
                    (deps.get(d) or {}).get('status') in ('failed', 'needs_review',
                                                          'skipped')
                    for d in (s.get('depends_on') or []))
                if s['node_id'] not in reached or dep_dead:
                    cur.execute(
                        "UPDATE ai_orchestration_steps SET status='skipped', "
                        "  finished_at=NOW(), updated_at=NOW() WHERE id=%s",
                        (s['id'],))
                    changed = True

    # 2) runnable：可达 + 全部依赖 succeeded
    for s in steps:
        if s['status'] != 'blocked' or s['node_id'] not in reached:
            continue
        deps_ok = all(
            by_node[d]['status'] == 'succeeded'
            for d in (s.get('depends_on') or [])
            if d in by_node)
        if not deps_ok:
            continue
        if s['kind'] == 'join':
            with get_db() as conn2:
                with conn2.cursor() as cur:
                    cur.execute(
                        "UPDATE ai_orchestration_steps SET status='succeeded', "
                        "  finished_at=NOW(), updated_at=NOW() WHERE id=%s",
                        (s['id'],))
                conn2.commit()
            changed = True
            continue
        if s['kind'] == 'approval':
            _open_approval(run, s)
            changed = True
            continue
        if s['kind'] == 'agent':
            _launch_agent_step(run, s, by_node)
            changed = True

    # 3) run 状态派生（spec §6.1 第 8 步）
    fresh = get_run(run_id)
    statuses = [s['status'] for s in fresh['steps']]
    if any(st == 'waiting_approval' for st in statuses):
        new_status = 'waiting_approval'
    elif any(st in ('running',) for st in statuses):
        new_status = 'running'
    elif any(st == 'blocked' for st in statuses):
        new_status = 'running'
    else:
        ok = statuses.count('succeeded')
        if ok == len(statuses):
            new_status = 'completed'
        elif 'failed' in statuses or 'needs_review' in statuses:
            new_status = 'partial' if ok else (
                'needs_review' if 'needs_review' in statuses else 'failed')
        else:
            new_status = 'completed'
    with get_db() as conn3:
        with conn3.cursor() as cur:
            cur.execute(
                "UPDATE ai_orchestration_runs SET status = %s, "
                "  started_at = COALESCE(started_at, "
                "    CASE WHEN %s IN ('running','waiting_approval') "
                "         THEN NOW() END), "
                "  finished_at = CASE WHEN %s IN ('completed','partial',"
                "    'failed','needs_review','cancelled') THEN NOW() "
                "    ELSE NULL END "
                "WHERE id = %s",
                (new_status, new_status, new_status, run_id))
        conn3.commit()
    if changed or new_status != run['status']:
        batch_events.append_event(run_id, 'run.status',
                                  aggregate_type='run', aggregate_id=run_id,
                                  payload={'status': new_status})


def _run_edges(run: dict) -> list[dict]:
    from utils import orchestration_defs
    d = orchestration_defs.get_definition(run['definition_id'],
                                          run['definition_version'])
    return (d or {}).get('edges') or []


def _launch_agent_step(run: dict, step: dict, by_node: dict):
    """agent step → 创建子会话（pending），由批 worker 认领执行。

    H4：step 的 runnable→running 转移用状态谓词 CAS——并发推进时只有
    一个线程能赢得转移，输者直接返回，不再重复创建子会话（审查实测
    单节点 run 并发创建 6 个子会话即此缺陷）。"""
    import uuid as _uuid
    from db import get_db
    node = step.get('node_def') or {}
    if step.get('attempt_count') and step.get('session_id'):
        # 重试：复用原子会话，换代重跑
        sid = step['session_id']
        prompt = (node.get('retry_prompt')
                  or '上一步未通过校验，请根据错误信息重试并给出完整结果。')
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET status='pending', "
                    "  continue_prompt=%s, error_message=NULL, "
                    "  execution_generation = execution_generation + 1 "
                    "WHERE id=%s", (prompt, sid))
                cur.execute(
                    "UPDATE ai_orchestration_steps SET status='running', "
                    "  attempt_count = attempt_count + 1, updated_at=NOW() "
                    "WHERE id=%s AND status='running'", (step['id'],))
            conn.commit()
        return
    sid = str(_uuid.uuid4())
    prompt = _render_prompt(node.get('prompt_template') or '', run, by_node)
    with get_db() as conn:
        with conn.cursor() as cur:
            # CAS：仅当 step 仍为首派候选 blocked 时赢得派发权；输者放弃
            # 创建，杜绝重复派发。10 号 §4：不收容 failed——failed 且有
            # attempt_count/session_id 的重试走上方复用分支（CAS 'running'），
            # 其余 failed 形态（blocked 快照后并发转 failed）不再复位
            # attempt_count、不再弃用原会话新建
            cur.execute(
                "UPDATE ai_orchestration_steps SET status='running', "
                "  session_id=%s, attempt_count = 1, started_at=NOW(), "
                "  updated_at=NOW() "
                "WHERE id=%s AND status='blocked' RETURNING id",
                (sid, step['id']))
            won = cur.fetchone() is not None
            if won:
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, title, status, workspace_path, session_token, "
                    "   token_expires_at, continue_prompt, agent, model, "
                    "   orchestration_run_id, orchestration_step_id) "
                    "VALUES (%s, %s, %s, 'pending', '', %s, NOW() + interval "
                    "  '24 hours', %s, %s, %s, %s, %s)",
                    (sid, run['requested_by'],
                     f"[编排] {run['id'][:8]} · {step.get('name') or step['node_id']}",
                     secrets.token_hex(32), prompt, node.get('agent'),
                     node.get('model'), run['id'], step['id']))
        conn.commit()
    if not won:
        return
    from utils import batch_events
    batch_events.append_event(run['id'], 'step.launched',
                              aggregate_type='step', aggregate_id=step['id'],
                              payload={'sessionId': sid})


def _open_approval(run: dict, step: dict):
    """approval step → 创建审批请求（幂等：uniq pending per step）。"""
    import uuid as _uuid
    from db import get_db
    node = step.get('node_def') or {}
    appr = node.get('approval') or {}
    aid = 'apr_' + secrets.token_hex(6)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai_approval_requests "
                    "  (id, run_id, step_id, risk_level, effect_summary, "
                    "   requested_roles, requested_users, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, "
                    "  CASE WHEN %s IS NULL THEN NULL "
                    "       ELSE NOW() + (%s || ' seconds')::interval END)",
                    (aid, run['id'], step['id'],
                     appr.get('risk_level') or 'medium',
                     node.get('name') or step['node_id'],
                     json.dumps(appr.get('requested_roles') or []),
                     json.dumps(appr.get('requested_users') or []),
                     appr.get('timeoutSeconds'),
                     str(int(appr.get('timeoutSeconds') or 0) or 0)))
                cur.execute(
                    "UPDATE ai_orchestration_steps SET status='waiting_approval', "
                    "  updated_at=NOW() WHERE id=%s", (step['id'],))
            conn.commit()
        from utils import batch_events
        batch_events.append_event(run['id'], 'approval.requested',
                                  aggregate_type='step',
                                  aggregate_id=step['id'],
                                  payload={'approvalId': aid})
    except Exception:
        # 并发下第二个 pending 插入被唯一索引挡住 → 已有请求在 pending，跳过
        logger.debug('approval already pending step=%s', step['id'], exc_info=True)


# ---------------------------------------------------------------------------
# step 终态回调（批 worker 驱动）
# ---------------------------------------------------------------------------

def _run_owner(run_id: str):
    run = get_run(run_id)
    return (run or {}).get('requested_by')


def on_child_terminal(run_id: str, step_id: str, session_id: str) -> None:
    """批 worker 在编排子会话到终态后调用：按子会话事实推进 step。
    合同校验（P2 §9.2 最小集）：output_contract.type='file' 检查产物存在。"""
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, error_message, workspace_path "
                        "FROM ai_chat_sessions WHERE id = %s", (session_id,))
            row = cur.fetchone()
    if not row:
        return
    child_status, child_err, ws = row
    # 复核报告 §4.3：非终态一律返回——worker 的自动重试会把子会话改回
    # pending 后 return（自愈流程），此刻若把 pending 判成 failed，一次
    # 本可自愈的重试就会杀死 step / 下游 / 整个 run。paused 同理（用户
    # 主动暂停，等待 resume）。
    if child_status in ('running', 'pending', 'paused'):
        return
    contract_ok = True
    contract_err = None
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT node_def FROM ai_orchestration_steps "
                        "WHERE id = %s", (step_id,))
            srow = cur.fetchone()
    node = (srow[0] or {}) if srow else {}
    contract = node.get('output_contract') or {}
    if child_status == 'completed' and contract.get('type') == 'file':
        from utils.workspace_outputs import list_session_files
        files = list_session_files(ws) if ws else []
        names = {f['name'] for f in (files[0] if isinstance(files, tuple) else files)}
        pat = (contract.get('path') or '').split('/')[-1]
        import fnmatch
        contract_ok = any(fnmatch.fnmatch(n, pat) for n in names) if pat else bool(names)
        contract_err = None if contract_ok else f'产物未通过合同校验: {pat}'

    if child_status == 'completed' and contract_ok:
        new_status, err = 'succeeded', None
    elif child_status == 'completed':
        new_status, err = 'failed', contract_err
    elif child_status == 'needs_review':
        new_status, err = 'needs_review', child_err
    else:
        # failed / cancelled：预算内重试
        retry_max = int((node.get('retry') or {}).get('max') or 0)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT attempt_count FROM ai_orchestration_steps "
                            "WHERE id = %s", (step_id,))
                attempts = cur.fetchone()[0]
        if attempts <= retry_max and child_status == 'failed':
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ai_orchestration_steps SET status='running', "
                        "  updated_at=NOW() WHERE id=%s", (step_id,))
                conn.commit()
            _launch_agent_step(_need_run(run_id),
                               {**({'id': step_id, 'node_def': node,
                                    'attempt_count': attempts,
                                    'session_id': session_id}
                                   )}, {})
            from utils import batch_events
            batch_events.append_event(run_id, 'step.retry',
                                      aggregate_type='step',
                                      aggregate_id=step_id)
            _advance_run(run_id)
            return
        new_status, err = 'failed', child_err or f'子会话终态 {child_status}'

    # H5 修复：成功时提取子会话最终 assistant 文本写入 step.output——
    # 条件边判定与 {{steps.x}} 提示词渲染的数据来源（此前全仓无写入点，
    # 条件分支恒不命中、下游渲染恒为空）
    step_output = None
    if new_status == 'succeeded':
        try:
            from utils.ai_scan_engine import message_text
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT content FROM ai_chat_messages "
                        " WHERE session_id = %s AND role = 'assistant' "
                        " ORDER BY seq DESC LIMIT 1", (session_id,))
                    row = cur.fetchone()
            texts = [p.get('text', '') for p in ((row[0] or []) if row else [])
                     if isinstance(p, dict) and p.get('type') == 'text']
            if texts:
                step_output = {'text': '\n'.join(texts)[:4000]}
        except Exception:
            logger.debug('step output extract failed sid=%s', session_id,
                         exc_info=True)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_orchestration_steps SET status=%s, "
                "  output=COALESCE(%s::jsonb, output), "
                "  error_message=%s, finished_at=NOW(), updated_at=NOW() "
                "WHERE id=%s",
                (new_status,
                 json.dumps(step_output, ensure_ascii=False) if step_output else None,
                 err, step_id))
        conn.commit()
    # 产物收集（Phase D：workspace outputs → artifact store）
    if new_status == 'succeeded' and ws:
        try:
            from utils import artifact_store
            # M9：带 owner 归属，否则创建者本人下载 403
            artifact_store.ingest_session_outputs(session_id, ws,
                                                  run_id=run_id,
                                                  step_id=step_id,
                                                  owner_user_id=_run_owner(run_id))
        except Exception:
            logger.warning('artifact ingest failed step=%s', step_id,
                           exc_info=True)
    from utils import batch_events
    batch_events.append_event(run_id, 'step.finished',
                              aggregate_type='step', aggregate_id=step_id,
                              payload={'status': new_status,
                                       'error': (err or '')[:200]})
    _advance_run(run_id)


def _need_run(run_id: str) -> dict:
    run = get_run(run_id)
    if not run:
        raise ValueError(f'run not found: {run_id}')
    return run


# ---------------------------------------------------------------------------
# scheduler（持 scheduler 租约的推进线程）
# ---------------------------------------------------------------------------

class OrchestrationScheduler:
    POLL_SEC = float(os.getenv('AI_ORCH_POLL_SEC', '5'))

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._owner: str | None = None
        self._retry_thread: threading.Thread | None = None
        self._holds_lease = False

    # 复核报告 §4.4：抢占失败进入后台重试（同 N2 模式）
    RETRY_SEC = 5.0

    def start(self):
        if (self._thread and self._thread.is_alive()) or                 (self._retry_thread and self._retry_thread.is_alive()):
            return  # 已在调度或正在等待租约：不重复起线程
        from utils import execution_lease
        self._owner = execution_lease.owner_id()
        ok, _ = execution_lease.acquire('scheduler', self._owner,
                                        lease_kind='scheduler')
        if not ok:
            logger.warning('orchestration scheduler lease NOT acquired; '
                           'retrying every %ss until the lease becomes '
                           'available', self.RETRY_SEC)
            self._retry_thread = threading.Thread(
                target=self._acquire_retry, daemon=True,
                name='orchestration-scheduler-retry')
            self._retry_thread.start()
            return
        self._holds_lease = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name='orchestration-scheduler')
        self._thread.start()

    def _acquire_retry(self):
        from utils import execution_lease
        while not self._stop.is_set():
            if self._stop.wait(self.RETRY_SEC):
                return
            ok, _ = execution_lease.acquire('scheduler', self._owner,
                                            lease_kind='scheduler')
            if not ok:
                continue
            logger.info('orchestration scheduler lease acquired after retry; '
                        'starting scheduler loop')
            self._holds_lease = True
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name='orchestration-scheduler')
            self._thread.start()
            return

    def _loop(self):
        from utils import execution_lease
        logger.info('orchestration scheduler started')
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception('orchestration tick failed')
            if not execution_lease.heartbeat('scheduler', self._owner):
                logger.warning('orchestration scheduler lease lost; exiting')
                self._holds_lease = False
                break
            self._stop.wait(self.POLL_SEC)
        logger.info('orchestration scheduler exited')

    def tick(self):
        """推进全部活跃 run + 审批超时处理。"""
        from db import get_db
        from utils import approval_repo
        approval_repo.expire_overdue()
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ai_orchestration_runs "
                    "WHERE status IN ('pending','running','waiting_approval')")
                run_ids = [r[0] for r in cur.fetchall()]
        for rid in run_ids:
            try:
                _advance_run(rid)
            except Exception:
                logger.exception('advance run failed id=%s', rid)

    def stop(self):
        self._stop.set()
        # retry 线程同样收口（10 号 §3.7：句柄保存 + join）
        for t_ in (self._thread, self._retry_thread):
            if t_ and t_.is_alive():
                t_.join(timeout=3)
        if self._holds_lease:
            from utils import execution_lease
            execution_lease.release('scheduler', self._owner)
            self._holds_lease = False


_SCHEDULER: OrchestrationScheduler | None = None


def start_scheduler():
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = OrchestrationScheduler()
    _SCHEDULER.start()
    return _SCHEDULER
