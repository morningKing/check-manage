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

from .approvals import _abort_timeout_child, _open_approval, _open_policy_approval


logger = logging.getLogger(__name__)

RUN_ACTIVE = ('pending', 'running', 'waiting_approval', 'recovering')
STEP_TERMINAL = ('succeeded', 'failed', 'skipped', 'needs_review')


# ---------------------------------------------------------------------------
# Run 创建（冻结定义版本 + 输入快照）
# ---------------------------------------------------------------------------

def create_run(def_id: str, requested_by: str, *,
               run_input: dict | None = None,
               requested_by_kind: str = 'user') -> dict:
    from ai.orchestration import orchestration_defs as orchestration_defs
    d = orchestration_defs.get_definition(def_id)
    if not d:
        raise ValueError('definition not found')
    run_id = 'run_' + secrets.token_hex(7)
    from db import get_db
    # P2 §5.6（缺口补齐 3.2）：run 创建即冻结 runtime manifest——当前环境
    # 为 OpenCodeLocal（默认 runtime）的能力快照 + 模型配置，供恢复/审计
    # 解释"当时用什么环境跑的"
    manifest_id = None
    try:
        from ai.runtime import get_runtime
        import hashlib as _hashlib
        _rt = get_runtime()
        _caps = _rt.capabilities()
        manifest_id = 'rtm_' + secrets.token_hex(7)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai_runtime_manifests "
                    "  (id, runtime_kind, runtime_version, resource_profile) "
                    "VALUES (%s, %s, %s, %s::jsonb)",
                    (manifest_id, _rt.kind,
                     getattr(_rt, 'version', None) or 'local',
                     json.dumps({'capabilities': _caps},
                                ensure_ascii=False, default=str)))
            conn.commit()
    except Exception:
        manifest_id = None  # manifest 失败不阻断 run 创建（观测性数据）
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_orchestration_runs "
                "  (id, definition_id, definition_version, status, "
                "   run_input_snapshot, requested_by, requested_by_kind, "
                "   runtime_manifest_id) "
                "VALUES (%s, %s, %s, 'pending', %s::jsonb, %s, %s, %s)",
                (run_id, def_id, d['version'],
                 json.dumps(run_input or {}, ensure_ascii=False),
                 requested_by, requested_by_kind, manifest_id))
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
    from ai.batch import batch_events as batch_events
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
    from ai.batch import batch_events as batch_events
    from .steps import _launch_agent_step
    run = get_run(run_id)
    if not run or run['status'] not in RUN_ACTIVE:
        return
    steps = run['steps']
    by_node = {s['node_id']: s for s in steps}
    edges = _run_edges(run)
    nodes_def = {s['node_id']: s for s in steps}
    # P3 单步调试：single_step 模式下每次 _advance_run 只派发 1 个 agent step
    execution_mode = run.get('execution_mode') or 'auto'
    single_step_launched = False

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

    # P2-A5：step 超时兜底——子会话卡死（hang/进程丢失）时 step 不再永久
    # running、run 不再永久 running。阈值：节点 timeout_sec → definition
    # timeout_policy.default_sec → 默认 900s。判定与转移在同一条 SQL 内
    # 完成（CAS status='running' + 时间谓词），并发推进时输者 0 行生效。
    # 只看 running：waiting_approval 由审批自身超时（expire_overdue）负责，
    # join/approval 结构节点不占执行槽、不走这里。
    timeout_default = 900
    try:
        from ai.orchestration import orchestration_defs as _odef
        _d = _odef.get_definition(run['definition_id'],
                                  run['definition_version'])
        timeout_default = int(((_d or {}).get('timeout_policy') or {})
                              .get('default_sec') or 900)
    except Exception:
        timeout_default = 900
    timed_out = []
    with get_db() as conn:
        with conn.cursor() as cur:
            for s in steps:
                if s['status'] != 'running':
                    continue
                t_sec = ((s.get('node_def') or {}).get('timeout_sec')
                         or timeout_default)
                try:
                    t_sec = int(t_sec)
                except (TypeError, ValueError):
                    t_sec = timeout_default
                cur.execute(
                    "UPDATE ai_orchestration_steps SET status='failed', "
                    "  error_message=%s, finished_at=NOW(), updated_at=NOW() "
                    "WHERE id=%s AND status='running' AND started_at IS NOT NULL "
                    "  AND started_at + (%s || ' seconds')::interval < NOW()",
                    (f'step timeout ({t_sec}s)', s['id'], str(t_sec)))
                if cur.rowcount:
                    # 同轮内存可见：下游 skip 传播/run 派生直接按 failed 走
                    s['status'] = 'failed'
                    timed_out.append((s['id'], t_sec, s.get('session_id')))
                    changed = True
    for _sid, _t, _child in timed_out:
        # 遗留项 3：超时兜底不只标 failed——子会话若已在 OpenCode 发出回合，
        # 不 abort 会滞留 running 直到对账器接管。best-effort：abort 失败
        # 不影响已落地的 step 转移（batch_engine 取消路径同模式）。
        _abort_timeout_child(_child)
        batch_events.append_event(run_id, 'step.timeout',
                                  aggregate_type='step', aggregate_id=_sid,
                                  payload={'timeoutSec': _t,
                                           'sessionId': _child})

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
                # P2-A6：join_policy='any_success' 的 join 不因单侧依赖
                # skip/failed 提前死亡——仍有非终态依赖（成功的那个可能还在
                # 路上）时继续等；全部依赖终态且无一成功时才允许 skip
                # （run 不卡死）。
                if (dep_dead and s['kind'] == 'join'
                        and ((s.get('node_def') or {}).get('join_policy')
                             or 'all_success') == 'any_success'
                        and any((deps.get(d) or {}).get('status')
                                not in STEP_TERMINAL
                                for d in (s.get('depends_on') or [])
                                if d in by_node)):
                    dep_dead = False
                if s['node_id'] not in reached or dep_dead:
                    cur.execute(
                        "UPDATE ai_orchestration_steps SET status='skipped', "
                        "  finished_at=NOW(), updated_at=NOW() WHERE id=%s",
                        (s['id'],))
                    changed = True

    # 2) runnable：可达 + 全部依赖 succeeded。
    # 调度（P2 §10 最小集，缺口补齐）：按节点声明的 priority 降序处理
    # （同一轮内高优先级 step 先占启动名额）。
    import os as _os
    launch_budget = int(_os.getenv('AI_ORCH_MAX_LAUNCH_PER_TICK', '8'))
    steps_sorted = sorted(
        steps, key=lambda s: -int((s.get('node_def') or {}).get('priority') or 0))
    for s in steps_sorted:
        if s['status'] != 'blocked' or s['node_id'] not in reached:
            continue
        dep_ids = [d for d in (s.get('depends_on') or []) if d in by_node]
        if s['kind'] == 'join':
            # P2-A6：join 汇聚语义——all_success（默认）要求全部依赖成功；
            # any_success 只要有任一依赖成功即通过（一侧条件分支 skip/failed
            # 不再永久阻塞 join）。
            policy = ((s.get('node_def') or {}).get('join_policy')
                      or 'all_success')
            dep_statuses = [by_node[d]['status'] for d in dep_ids]
            deps_ok = (('succeeded' in dep_statuses)
                       if policy == 'any_success'
                       else all(st == 'succeeded' for st in dep_statuses))
        else:
            deps_ok = all(by_node[d]['status'] == 'succeeded' for d in dep_ids)
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
            # 策略拦截（缺口补齐：P2 §7.1 策略拦截最小集）：节点声明
            # approval_policy → 派发前先建审批；该 step 已存在 approved
            # 请求时豁免（审批放行后不再重复拦截）
            node_appr = (s.get('node_def') or {}).get('approval_policy')
            if node_appr:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT 1 FROM ai_approval_requests "
                            "WHERE step_id = %s AND status = 'approved' "
                            "LIMIT 1", (s['id'],))
                        released = cur.fetchone() is not None
                if not released:
                    _open_policy_approval(run, s, node_appr)
                    changed = True
                    continue
            # 背压（P2 §10）：每轮 _advance_run 的 agent 派发数有上限
            if launch_budget <= 0:
                continue
            # P3 单步调试：single_step 模式只允许派发 1 个 agent step/轮，
            # 后续步骤等用户显式 advance
            if execution_mode == 'single_step':
                if single_step_launched:
                    continue
                single_step_launched = True
            _launch_agent_step(run, s, by_node)
            launch_budget -= 1
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
    from ai.orchestration import orchestration_defs as orchestration_defs
    d = orchestration_defs.get_definition(run['definition_id'],
                                          run['definition_version'])
    return (d or {}).get('edges') or []



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
        from ai.batch import execution_lease as execution_lease
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
        from ai.batch import execution_lease as execution_lease
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
        from ai.batch import execution_lease as execution_lease
        logger.info('orchestration scheduler started')
        while not self._stop.is_set():
            try:
                self.tick()
                self._cleanup_stale_workspaces()
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
        from ai.orchestration import approval_repo as approval_repo
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

    def _cleanup_stale_workspaces(self):
        """P2-B4：终态子会话 workspace 过 TTL 后回收目录并清列。

        只碰 status 终态（completed/failed/needs_review/cancelled）且批次
        completed_at 已过 AI_SESSION_TTL_HOURS 的行——workspace 删除不可逆，
        运行中/暂停/软删除的一律不动。清列（workspace_path=NULL）与删目录
        同事务：retry-failed 不会再拿到指向已删目录的路径。全 best-effort，
        失败只记日志，不阻断编排推进。"""
        import shutil
        from config import AI_SESSION_TTL_HOURS
        if AI_SESSION_TTL_HOURS <= 0:
            return
        cutoff_hours = AI_SESSION_TTL_HOURS
        try:
            from db import get_db
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT s.id, s.workspace_path
                        FROM ai_chat_sessions s
                        JOIN ai_chat_batches b ON s.batch_id = b.id
                        WHERE s.status IN ('completed','failed','needs_review','cancelled')
                          AND s.workspace_path IS NOT NULL
                          AND s.deleted_at IS NULL
                          AND b.completed_at < NOW() - (%s || ' hours')::interval
                    """, (str(cutoff_hours),))
                    rows = cur.fetchall()
                    for sid, ws_path in rows:
                        if ws_path and os.path.isdir(ws_path):
                            shutil.rmtree(ws_path, ignore_errors=True)
                        cur.execute(
                            "UPDATE ai_chat_sessions SET workspace_path = NULL "
                            "WHERE id = %s", (sid,))
                conn.commit()
            if rows:
                logger.info('workspace TTL cleanup: reclaimed %d stale '
                            'workspace(s)', len(rows))
        except Exception:
            logger.exception('workspace TTL cleanup failed')

    def stop(self):
        self._stop.set()
        # retry 线程同样收口（10 号 §3.7：句柄保存 + join）
        for t_ in (self._thread, self._retry_thread):
            if t_ and t_.is_alive():
                t_.join(timeout=3)
        if self._holds_lease:
            from ai.batch import execution_lease as execution_lease
            execution_lease.release('scheduler', self._owner)
            self._holds_lease = False


_SCHEDULER: OrchestrationScheduler | None = None


def start_scheduler():
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = OrchestrationScheduler()
    _SCHEDULER.start()
    return _SCHEDULER


def run_graph(run_id: str) -> dict | None:
    """run graph（P2 §10/§13，缺口补齐）：节点状态 + 耗时 + 聚合可观测，
    一次返回管理面渲染 DAG 所需的全部数据。"""
    run = get_run(run_id)
    if not run:
        return None
    import datetime as _dt
    nodes = []
    median_done = []
    for s in run['steps']:
        dur_ms = None
        if s.get('started_at') and s.get('finished_at'):
            try:
                dur_ms = int((s['finished_at'] - s['started_at']).total_seconds() * 1000)
                if dur_ms > 0:
                    median_done.append(dur_ms)
            except Exception:
                pass
        nodes.append({
            'nodeId': s['node_id'], 'stepId': s['id'], 'name': s.get('name'),
            'kind': s.get('kind'), 'status': s['status'],
            'attemptCount': s.get('attempt_count'),
            'startedAt': s['started_at'].isoformat() if s.get('started_at') else None,
            'finishedAt': s['finished_at'].isoformat() if s.get('finished_at') else None,
            'durationMs': dur_ms,
            'error': (s.get('error_message') or None),
            'outputPreview': ((s.get('output') or {}).get('text') or '')[:160],
        })
    edges = [{'source': e['source'], 'target': e['target'],
              'condition': e.get('condition')} for e in _run_edges(run)]
    median_done.sort()
    remaining = sum(1 for s in run['steps'] if s['status'] in ('blocked', 'running', 'pending'))
    median_ms = median_done[len(median_done) // 2] if median_done else None
    eta = median_ms * remaining if (median_ms and remaining) else None
    failed = sum(1 for s in run['steps'] if s['status'] == 'failed')
    # 成本：编排子会话共享 P1 usage 表（session_id 键）
    cost = 0.0
    try:
        from db import get_db as _g
        with _g() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(SUM(u.cost), 0) FROM ai_execution_usage u "
                    "WHERE u.session_id IN (SELECT id FROM ai_chat_sessions "
                    "WHERE orchestration_run_id = %s)", (run_id,))
                cost = float(cur.fetchone()[0])
    except Exception:
        pass
    return {'run': _out_run(run), 'nodes': nodes, 'edges': edges,
            'observability': {
                'failedSteps': failed, 'remainingSteps': remaining,
                'medianDurationMs': median_ms,
                'estimatedRemainingMs': eta, 'cost': cost}}


def _out_run(run: dict) -> dict:
    return {'runId': run['id'], 'definitionId': run.get('definition_id'),
            'definitionVersion': run.get('definition_version'),
            'status': run['status'],
            'createdAt': run['created_at'].isoformat() if run.get('created_at') else None,
            'finishedAt': run['finished_at'].isoformat() if run.get('finished_at') else None,
            'error': run.get('error_message')}



__all__ = [
    'logger',
    'RUN_ACTIVE',
    'STEP_TERMINAL',
    'create_run',
    'get_run',
    'list_runs',
    '_edge_matches',
    '_render_prompt',
    '_advance_run',
    '_advance_run_locked',
    '_run_edges',
    'OrchestrationScheduler',
    '_SCHEDULER',
    'start_scheduler',
    'run_graph',
    '_out_run',
]
