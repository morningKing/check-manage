import json
import logging
import secrets

from .runs import get_run, _advance_run, _render_prompt

logger = logging.getLogger(__name__)


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
                # P2-A5：换代重跑同时重置超时时钟（started_at）——否则重试
                # 会继承首跑的 started_at，被超时兜底立即误杀
                cur.execute(
                    "UPDATE ai_chat_sessions SET status='pending', "
                    "  continue_prompt=%s, error_message=NULL, "
                    "  execution_generation = execution_generation + 1 "
                    "WHERE id=%s", (prompt, sid))
                cur.execute(
                    "UPDATE ai_orchestration_steps SET status='running', "
                    "  attempt_count = attempt_count + 1, started_at=NOW(), "
                    "  updated_at=NOW() "
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
                # P3 门禁支持编排：节点声明 action_checks → 与子会话同一事务
                # 登记为期望行，终态由批 worker 的既有门禁核对评估（_run_one
                # 对编排子会话 batch_id=NULL 不登记批级期望、也不清除编排侧
                # 期望，gate_participates 按 evaluated/expected 参与判定）。
                # 同事务保证 worker 认领（commit 触发 notify）时期望已可见，
                # 不存在"有会话无期望"的 fail-open 窗口；登记失败回滚到保存
                # 点，不阻断派发、不影响会话创建。
                if node.get('action_checks'):
                    try:
                        cur.execute("SAVEPOINT orch_action_checks")
                        from contextlib import contextmanager as _cm
                        from ai.batch.ledger import (
                                                    register_session_expectations)

                        @_cm
                        def _same_conn():
                            yield conn

                        register_session_expectations(
                            sid, node['action_checks'],
                            source='orchestration', get_db=_same_conn)
                    except Exception:
                        cur.execute(
                            "ROLLBACK TO SAVEPOINT orch_action_checks")
                        logger.warning(
                            'orchestration step action_checks registration '
                            'failed sid=%s', sid, exc_info=True)
        conn.commit()
    if not won:
        return
    from ai.batch import batch_events as batch_events
    batch_events.append_event(run['id'], 'step.launched',
                              aggregate_type='step', aggregate_id=step['id'],
                              payload={'sessionId': sid})



# ---------------------------------------------------------------------------
# step 终态回调（批 worker 驱动）
# ---------------------------------------------------------------------------

def _run_owner(run_id: str):
    run = get_run(run_id)
    return (run or {}).get('requested_by')


def _compensate_step_effects(session_id: str, step_id: str):
    """C4：补偿 step 的已提交副作用（best-effort）。

    step 失败终态时回退该子会话已提交的 mcp_write POST 创建记录；
    PUT/DELETE 无逆操作、unknown 禁自动补偿——均由 compensate_effect
    内部把关（不满足条件的 effect 返回 None 跳过）。补偿失败只记日志，
    不影响 run 推进。"""
    from db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ai_execution_effects "
                    "WHERE session_id = %s AND status = 'committed' "
                    "  AND effect_type = 'mcp_write'",
                    (session_id,))
                effect_ids = [r[0] for r in cur.fetchall()]
            for eid in effect_ids:
                from ai.batch.execution_effect import compensate_effect
                compensate_effect(eid, conn=conn)
    except Exception:
        logger.exception('effect compensation failed session=%s', session_id)


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
    ctype = contract.get('type')
    if child_status == 'completed' and ctype == 'file':
        from projects.workspace_outputs import list_session_files
        files = list_session_files(ws) if ws else []
        names = {f['name'] for f in (files[0] if isinstance(files, tuple) else files)}
        pat = (contract.get('path') or '').split('/')[-1]
        import fnmatch
        contract_ok = any(fnmatch.fnmatch(n, pat) for n in names) if pat else bool(names)
        contract_err = None if contract_ok else f'产物未通过合同校验: {pat}'
    elif child_status == 'completed' and ctype == 'json_schema':
        # 批次 3：输出 text 须为合法 JSON 且过 jsonschema（缺库则退化为
        # 「合法 JSON 即通过」，与 spec §9.2 的结构化契约对齐）
        import json as _json
        try:
            _txt = None
            with get_db() as _c:
                with _c.cursor() as _cu:
                    _cu.execute(
                        "SELECT content FROM ai_chat_messages "
                        "WHERE session_id = %s AND role = 'assistant' "
                        "ORDER BY seq DESC LIMIT 1", (session_id,))
                    _row = _cu.fetchone()
            if _row:
                _content = _row[0]
                _parts = _json.loads(_content) if isinstance(_content, str) else _content
                _txt = ''.join(p.get('text') or ''
                               for p in (_parts or []) if isinstance(p, dict)
                               and p.get('type') == 'text')
            _doc = _json.loads(_txt) if _txt else None
        except Exception:
            _doc = None
        if _doc is None:
            contract_ok, contract_err = False, '输出不是合法 JSON，未通过 json_schema 合同'
        else:
            try:
                import jsonschema as _js
                _js.validate(_doc, contract.get('schema') or {})
                contract_ok, contract_err = True, None
            except ImportError:
                contract_ok, contract_err = True, None
            except Exception as _e:
                contract_ok = False
                contract_err = f'输出未通过 json_schema 合同: {str(_e)[:200]}'
    elif child_status == 'completed' and ctype == 'db_record':
        # 复用动作账本的 db-record 证据通道（与动作门禁同一事实源）
        try:
            from ai.batch import ledger as _al
            _eff = (contract.get('effect_spec')
                    or {'collection': contract.get('collection'),
                        'filter': contract.get('filter') or {}})
            with get_db() as _c:
                with _c.cursor() as _cu:
                    n = _al._count_db_record_evidence(_cu, _eff)
            contract_ok = n >= int(contract.get('min_count') or 1)
            contract_err = None if contract_ok else                 f'db_record 合同未达标: 命中 {n}/{contract.get("min_count") or 1}'
        except Exception as _e:
            contract_ok = False
            contract_err = f'db_record 合同核对异常: {str(_e)[:200]}'

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
            from ai.batch import batch_events as batch_events
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
    if new_status in ('succeeded', 'failed'):
        try:
            from ai.scan.ai_scan_engine import message_text
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
        # P2 §6.4 declared_plan：模型自声明的 Todo 计划只进观测字段
        # （step.output.declaredPlan），永远不参与 DAG 推进
        try:
            from ai.batch.todo_trace import build_declared_plan
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, role, content, created_at "
                        "FROM ai_chat_messages WHERE session_id = %s "
                        "ORDER BY seq", (session_id,))
                    _cols = ['id', 'role', 'content', 'created_at']
                    _msgs = [dict(zip(_cols, r)) for r in cur.fetchall()]
            _plan = build_declared_plan(_msgs)
            if _plan and step_output is not None:
                step_output['declaredPlan'] = {
                    'latest': _plan.get('latest') or [],
                    'statusChanges': _plan.get('statusChanges') or {}}
        except Exception:
            logger.debug('declared_plan extract failed sid=%s', session_id,
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
    # C4：step 失败终态时补偿该子会话已提交的 mcp_write POST 创建
    # （在 step 状态落库之后执行；重试路径已在上方提前 return，不补偿）
    if new_status == 'failed':
        _compensate_step_effects(session_id=session_id, step_id=step_id)
    # 产物收集（Phase D：workspace outputs → artifact store）
    if new_status == 'succeeded' and ws:
        try:
            from ai.sessions import artifact_store as artifact_store
            # M9：带 owner 归属，否则创建者本人下载 403
            artifact_store.ingest_session_outputs(session_id, ws,
                                                  run_id=run_id,
                                                  step_id=step_id,
                                                  owner_user_id=_run_owner(run_id))
        except Exception:
            logger.warning('artifact ingest failed step=%s', step_id,
                           exc_info=True)
    from ai.batch import batch_events as batch_events
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



__all__ = [
    'logger',
    '_launch_agent_step',
    '_run_owner',
    '_compensate_step_effects',
    'on_child_terminal',
    '_need_run',
]
