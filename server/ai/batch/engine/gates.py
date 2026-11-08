import json
import logging
import os
import secrets

from db import get_db
from ai.batch import ledger as agent_ledger
from ai.batch import verifier as verifier

from .facade import opencode_client

logger = logging.getLogger(__name__)

class WorkerGatesMixin:
    # --- 动作账本与到位门禁（设计:docs/design/AI子任务动作账本与到位门禁设计.md）---

    def _fetch_action_checks(self, batch_id: str):
        """入口 A/B/策略（spec §8.2）：合并事实源在 gate_policy_repo。"""
        from ai.gate.gate_policy_repo import merged_action_checks
        return merged_action_checks(batch_id)

    def _register_action_expectations(self, sid: str, batch_id: str,
                                      session_row: dict | None = None) -> dict:
        """派发前把批定义/模板的 action_checks 登记为期望行。带 apply_to
        条件的检查按子任务属性(batch_seq/输入文件名)过滤——不匹配的子任务
        不登记,避免不相关动作的门禁误报。

        P0 fail-closed（spec §8.1）：返回 {'applicable','registered','error'}
        供终态核对判定——登记异常不再"降级为 warning 后继续"，异常时
        error 非空，终态走 inconclusive → failed（不得 completed）。"""
        try:
            checks = self._fetch_action_checks(batch_id)
            if not checks:
                return {'applicable': 0, 'registered': 0, 'error': None}
            seq = (session_row or {}).get('batch_seq')
            infile = (session_row or {}).get('batch_input_file')
            checks = [c for c in checks
                      if agent_ledger.check_applies_to_child(c, seq, infile)]
            if not checks:
                return {'applicable': 0, 'registered': 0, 'error': None}
            registered = agent_ledger.register_session_expectations(
                sid, checks, source='batch', get_db=get_db)
            return {'applicable': len(checks), 'registered': registered,
                    'error': None}
        except Exception as e:  # noqa: BLE001 —— 登记失败必须可见（fail-closed）
            logger.warning('action expectation registration failed sid=%s: %s',
                           sid, e)
            return {'applicable': None, 'registered': 0, 'error': str(e)[:200]}

    def _check_action_gate(self, sid: str, *, workspace_path=None, model=None) -> dict:
        """终态核对。inconclusive(账本不健康/查询异常/判官故障)不算新语义:
        统一走既有 fail-closed 分支,任务落 failed、期望行与日志留痕。
        verifier 期望由判官核对(ai/batch/verifier,方案 B)——无 verifier 期望时
        run_verifier 返回 None,零开销跳过。委派级门禁（定向组子代理终态时
        提前判定）的暂存 verdict 先行合并，已判期望跳过重复判官。"""
        healthy = self._ledger_health.pop(sid, True)
        result = agent_ledger.check_session_gate(sid, ledger_healthy=healthy,
                                                 get_db=get_db)
        if healthy:
            skip_names = None
            cached = self._deleg_gate_results.pop(sid, None)
            if cached:
                try:
                    result = verifier.merge_verifier_results(
                        result, {'status': 'completed', 'error': None,
                                 'results': cached}, sid, get_db=get_db)
                    skip_names = [r['name'] for r in cached]
                except Exception:  # noqa: BLE001
                    logger.exception('deleg gate merge failed sid=%s', sid)
            run = None
            try:
                # 先探期望：无 verifier 期望时不惊动判官（零开销路径,与
                # run_verifier 内部的 None 早退同判据）。
                if verifier.collect_materials(sid, get_db=get_db) is not None:
                    run = verifier.run_verifier(sid, workspace_path=workspace_path,
                                                model=model, get_db=get_db,
                                                skip_names=skip_names)
            except Exception as e:  # noqa: BLE001 —— 钩子自身异常双保险
                logger.warning('verifier hook crashed sid=%s: %s', sid, e)
                mats = verifier.collect_materials(sid, get_db=get_db) or {}
                run = {'status': 'error', 'error': str(e)[:300],
                       'results': verifier._all_inconclusive(
                           [c['name'] for c in mats.get('checks', [])],
                           f'verifier 异常: {str(e)[:200]}')}
            if run is not None:
                result = verifier.merge_verifier_results(result, run, sid, get_db=get_db)
        if result['status'] == 'inconclusive':
            logger.warning('action gate inconclusive sid=%s: %s',
                           sid, result.get('error') or healthy is False)
        return result

    def _batch_gate_retry_flag(self, batch_id: str):
        """批级修正开关(NULL=未设置,跟随全局环境变量)。"""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT gate_retry FROM ai_chat_batches WHERE id = %s",
                            (batch_id,))
                row = cur.fetchone()
        return row[0] if row else None

    def _maybe_gate_retry(self, session_id: str, gate: dict,
                          batch_id: str | None = None,
                          generation: int | None = None) -> bool:
        """gate_retry:不过门时带"缺失明细"定向修复提示,在原会话上 continue
        续跑(原 agent、上下文保留,不重做已完成部分)。开关优先级:批级
        gate_retry 列 > 全局环境变量 AI_BATCH_GATE_RETRY(默认 0=关闭);
        批级开启时预算至少 1 次,与 auto-retry 共用 retry_count。"""
        cap = int(os.getenv('AI_BATCH_GATE_RETRY', '0') or 0)
        if batch_id is not None:
            flag = self._batch_gate_retry_flag(batch_id)
            if flag is True:
                cap = max(cap, 1)
            elif flag is False:
                return False
        if cap <= 0:
            return False
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT retry_count, opencode_session_id FROM ai_chat_sessions "
                    "WHERE id = %s", (session_id,))
                row = cur.fetchone()
                if row is None:
                    return False
                retry_count, oc = row
                if retry_count >= cap:
                    return False
                repair = ('上一轮任务已完成,但动作门禁核对未通过:'
                          + agent_ledger.gate_failure_message(gate)
                          + '。请只补齐上述缺失的动作,不要重做已完成的部分;'
                          '完成后正常收尾。')
                cur.execute(
                    "UPDATE ai_chat_sessions "
                    "SET status='pending', retry_count = retry_count + 1, "
                    "    error_message=NULL, cancel_requested=false, pause_requested=false, "
                    "    execution_generation = execution_generation + 1, "
                    "    active_turn_id = NULL, "
                    "    continue_prompt = CASE WHEN %s IS NOT NULL THEN %s "
                    "                      ELSE continue_prompt END "
                    "WHERE id = %s AND status = 'running' "
                    "  AND NOT cancel_requested AND NOT pause_requested",
                    (oc, repair, session_id))
                requeued = cur.rowcount > 0
        if not requeued:
            # F1：同 _maybe_auto_retry——终态行不复活，用户取消/暂停不吞。
            logger.info('action gate retry skipped sid=%s '
                        '(row no longer requeueable)', session_id)
            return False
        self._close_attempt_for_requeue(session_id, 'GATE_RETRY',
                                        error_message=repair)
        logger.warning('action gate retry sid=%s (re-queued with repair prompt)',
                       session_id)
        return True

    def _fetch_subagent_reuse(self, batch_id: str) -> list:
        """批级子代理会话复用名单（agent 名数组；未配置返回空）。"""
        if not batch_id:
            return []
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT subagent_reuse FROM ai_chat_batches WHERE id = %s",
                        (batch_id,),
                    )
                    row = cur.fetchone()
            return [a for a in (row[0] or []) if a] if row else []
        except Exception:  # noqa: BLE001 —— 配置读取失败按不启用处理
            return []

    def _maybe_check_delegation_gate(self, session_id: str, directory: str,
                                     known: dict, raw: list | None = None):
        """委派级门禁（设计 §5.4 扩展，2026-09-30）：verifier 定向组（期望带
        subagents 名单）在名单内子代理全部终态时提前判定。

        材料完整性守卫（2026-10-02）：仅当父回合已 finished（模型已收尾，
        不会再有新委派）才判定——子代理终态但父回合仍在跑时材料可能不完整
        （rubric 提及的后续委派尚未发生），提前判会把「应有两委派只见到一」
        误判成 failed 并误停达标任务（生产 e2e 实测）。未 finished 时跳过
        本拍，等下一拍重查。

        - 全部 passed → 只暂存（终态合并为 gate passed，跳过重复判官）；
        - failed 且批级 gate_retry 开 → 暂存（终态合并为 gate failed → 引擎
          定向修复分支照常生效）；
        - failed 且未开 gate_retry → 写 cancel_requested 立即停止该子任务
          （轮询下一拍抛 _SessionCancelled，走既有取消收口），不再让它带着
          已注定的失败继续烧 token。

        判官同步执行会暂停轮询线程一个判官回合（10~60s），期间 _gate_checking
        豁免 tool-stuck 看门狗。防御式：任何异常只记日志，不影响主持久化。"""
        try:
            if not known:
                return
            # 父回合 finished 感知：finished 前 material 可能不完整，跳过判定
            if raw is not None:
                last_assistant_fin = False
                for m in raw:
                    info = m.get('info') or {}
                    if info.get('role') == 'assistant':
                        fin = (info.get('time') or {}).get('completed')
                        finish = info.get('finish')
                        last_assistant_fin = bool(
                            fin) and finish not in ('tool-calls', 'tool_use')
                if not last_assistant_fin:
                    return
            done = self._deleg_gate_done.setdefault(session_id, {})
            # 名单内子代理：本次快照里终态且尚未核对过
            targets = [(st_id, info) for st_id, info in known.items()
                       if info.get('status') in ('completed', 'failed')
                       and (st_id, session_id) not in done
                       and info.get('agent')]
            if not targets:
                return
            from db import get_db as _gdb
            with _gdb() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT name, effect_spec, subagents FROM action_expectations "
                        "WHERE scope_id = %s AND scope_type IN ('session','tree') "
                        "  AND check_type = 'verifier'", (session_id,))
                    rows = cur.fetchall()
            if not rows:
                for st_id, _info in targets:
                    done[(st_id, session_id)] = True
                return
            # 按 subagents 名单组合分组（与 run_verifier 的分组口径一致；
            # 条目构造共用 verifier.check_row——机械复核 markers 随行，护栏
            # 在委派级判定同样生效）
            from ai.batch import verifier as _v
            buckets: dict = {}
            for name, effect_spec, subagents in rows:
                key = tuple(sorted({str(a).strip() for a in (subagents or []) if str(a).strip()}))
                if key == (None,):
                    continue                      # 全树组：终态收尾统一核对
                buckets.setdefault(key, []).append(_v.check_row(name, effect_spec))
            if not buckets:
                for st_id, _info in targets:
                    done[(st_id, session_id)] = True
                return
            # 每组：名单内子代理是否都已终态且在本次 targets 中
            cur_status = {st_id: info.get('status') for st_id, info in known.items()}
            self._gate_checking.add(session_id)
            try:
                for key, checks in buckets.items():
                    members = [(st_id, info) for st_id, info in known.items()
                               if (info.get('agent') or '') in key]
                    if not members or not all(
                            cur_status.get(st_id) in ('completed', 'failed')
                            for st_id, _i in members):
                        continue                  # 名单内还有子代理未结束：等下一拍
                    if all((st_id, session_id) in done for st_id, _i in members):
                        continue                  # 已核对过
                    group = {'agents': key, 'checks': checks,
                             'subagent_segments': [], 'trace': []}
                    # 材料组装（spec §4 定向组口径，同 verifier.collect_materials
                    # 的查询/截断上限；渲染走 build_verifier_prompt，形状必须与
                    # 终态路径一致）。此前两键恒为空——判官拿到的材料是空的
                    # （「该名单下没有发现任何子代理会话…无工具调用记录」），
                    # 达标与否全看判官宽松度，负向场景必然误判 failed → 误停。
                    # 组装失败跳过本组本拍（不标 done）：拿空材料判定会重蹈误停。
                    try:
                        with _gdb() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "SELECT id, agent FROM ai_chat_subtasks "
                                    "WHERE root_session_id = %s AND agent = ANY(%s) "
                                    "ORDER BY created_at, id",
                                    (session_id, list(key)))
                                segments, st_ids = [], []
                                for st_id, st_agent in cur.fetchall():
                                    st_ids.append(st_id)
                                    cur.execute(
                                        "SELECT role, content FROM ai_chat_subtask_messages "
                                        "WHERE subtask_id = %s ORDER BY seq LIMIT %s",
                                        (st_id, _v._MAX_SUBAGENT_MSG_ROWS))
                                    segments.append({
                                        'agent': st_agent, 'subtask_id': st_id,
                                        'messages': [
                                            {'role': r[0],
                                             'text': _v._parts_text(r[1])[:_v._MAX_SUBAGENT_MSG_CHARS]}
                                            for r in cur.fetchall()]})
                                if st_ids:
                                    cur.execute(
                                        "SELECT oc_session_id, tool, args_text, state, "
                                        "       occurred_at "
                                        "FROM agent_tool_calls WHERE root_session_id = %s "
                                        "  AND subtask_id = ANY(%s) "
                                        "ORDER BY occurred_at, id LIMIT %s",
                                        (session_id, st_ids, _v._MAX_TRACE_ROWS))
                                    group['trace'] = _v._trace_rows(cur.fetchall())
                                group['subagent_segments'] = segments
                    except Exception as e:  # noqa: BLE001 —— 材料不全不判定
                        logger.warning('delegation gate material build failed '
                                       'sid=%s agents=%s: %s',
                                       session_id, ','.join(key), e)
                        continue
                    # 判官客户端用真 OpenCodeClient（与终态核对同源）——本模块的
                    # opencode_client 是 _OpenCodeFacade 门面，未包装
                    # send_prompt_async，传过去会让判官会话创建恒失败。
                    run = _v._run_group(_v._default_client(), group, directory,
                                        self._deleg_gate_model(session_id),
                                        session_id,
                                        _v.DEFAULT_TIMEOUT_SEC, _v.DEFAULT_STALL_SEC)
                    for st_id, _i in members:
                        done[(st_id, session_id)] = True
                    # 落库持久证据：failed 判定即写 fit_results 行——即停路径
                    # （cancelled）不走终态 gate 核对，没有这里就无法在拟合
                    # 摘要/版本聚合中看到即停任务的判官结果。passed 暂存即可
                    # （终态 merge 落库）。def_hash 期望级缺失置 NULL（列可空）。
                    # attempt_id 现查最新 attempt（同 verifier.merge_verifier_results
                    # 口径；此前引用了未定义的 attempt 名——INSERT 恒 NameError，
                    # 被裸 except 吞掉，拟合行从未落过）。
                    att_id = None
                    try:
                        with _gdb() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "SELECT id FROM ai_execution_attempts "
                                    "WHERE session_id = %s "
                                    "ORDER BY started_at DESC NULLS LAST LIMIT 1",
                                    (session_id,))
                                row = cur.fetchone()
                                att_id = row[0] if row else None
                    except Exception as e:  # noqa: BLE001
                        logger.warning('deleg gate fit attempt lookup failed '
                                       'sid=%s: %s', session_id, e)
                    landed = [x for x in run['results']
                              if x['status'] in ('passed', 'failed')]
                    for x in landed:
                        if x['status'] != 'failed' or not att_id:
                            continue
                        try:
                            with _gdb() as conn2:
                                with conn2.cursor() as cur2:
                                    cur2.execute(
                                        "INSERT INTO ai_skill_fit_results "
                                        "(id, attempt_id, session_id, def_kind, "
                                        " def_name, def_hash, steps_total, "
                                        " steps_hit, score, status, per_step) "
                                        "VALUES (%s, %s, %s, 'verifier', %s, NULL, "
                                        " %s, %s, %s, 'failed', %s::jsonb) "
                                        "ON CONFLICT (attempt_id, def_name) DO UPDATE SET "
                                        " score = EXCLUDED.score, "
                                        " status = 'failed', "
                                        " per_step = EXCLUDED.per_step, "
                                        " computed_at = NOW()",
                                        ('fit_' + secrets.token_hex(6), att_id,
                                         session_id, x['name'], len(run['results']),
                                         sum(1 for y in run['results']
                                             if y['status'] == 'passed'),
                                         0,
                                         json.dumps(run['results'],
                                                    ensure_ascii=False)))
                                conn2.commit()
                        except Exception as e:
                            logger.warning('deleg gate fit row persist failed '
                                           'sid=%s def=%s: %s',
                                           session_id, x['name'], e)
                    cached = self._deleg_gate_results.setdefault(session_id, [])
                    cached.extend(landed)
                    failed_names = [x['name'] for x in landed
                                    if x['status'] == 'failed']
                    if not failed_names:
                        if any(x['status'] == 'inconclusive'
                               for x in run['results']):
                            logger.info('delegation gate inconclusive sid=%s '
                                        'agents=%s（材料不足，终态重判）',
                                        session_id, ','.join(key))
                        continue
                    # gate_retry 语义在终态合并处生效；这里只做「未开修正→立即停」
                    bid = self._deleg_gate_batch(session_id)
                    retry_on = False
                    if bid:
                        retry_on = bool(self._batch_gate_retry_flag(bid))
                    if retry_on:
                        logger.info(
                            'delegation gate failed (deferred to gate_retry) '
                            'sid=%s agents=%s checks=%s', session_id,
                            ','.join(key), ','.join(failed_names))
                        continue
                    logger.warning(
                        'delegation gate failed → stopping child sid=%s '
                        'agents=%s checks=%s', session_id, ','.join(key),
                        ','.join(failed_names))
                    with _gdb() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE ai_chat_sessions SET cancel_requested = true "
                                "WHERE id = %s AND status = 'running'",
                                (session_id,))
                        conn.commit()
                    return                       # 已请求停止，本拍不再核对
            finally:
                self._gate_checking.discard(session_id)
        except Exception:  # noqa: BLE001 —— 委派级门禁失败不影响主持久化
            logger.exception('delegation gate check failed sid=%s', session_id)

    def _deleg_gate_batch(self, session_id: str) -> str | None:
        """子会话所属批任务 id（委派级门禁用；单行轻查）。"""
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT batch_id FROM ai_chat_sessions WHERE id = %s",
                                (session_id,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception:  # noqa: BLE001
            return None

    def _deleg_gate_model(self, session_id: str) -> str:
        """委派级判官用批任务配置的模型（与主核对同源）。"""
        bid = self._deleg_gate_batch(session_id)
        if not bid:
            return ''
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT model FROM ai_chat_batches WHERE id = %s", (bid,))
                    row = cur.fetchone()
            return row[0] or '' if row else ''
        except Exception:  # noqa: BLE001
            return ''

    def _build_reuse_directive(self, root_session_id: str,
                               agents: list) -> str:
        """复用指令（引导层；强制层是 OC 插件的 task_id 注入）。

        已有钉住会话的 agent 要求模型显式携带 task_id；未钉住的说明首次
        委派，无需携带（插件 after 回调会登记锚点）。"""
        if not agents:
            return ''
        pins = {}
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT agent, task_id FROM ai_subagent_pins "
                        "WHERE root_session_id = %s AND agent = ANY(%s)",
                        (root_session_id, list(agents)),
                    )
                    for a, t in cur.fetchall():
                        pins[a] = t
        except Exception:  # noqa: BLE001
            return ''
        if not pins:
            return ''
        lines = [f'- {a}: task_id: {t}' for a, t in pins.items()]
        header = ('[子代理会话复用规则] 本任务启用了子代理会话复用。用 task 工具委派'
                  '以下 agent 时，必须在调用参数中携带对应的 task_id（续跑同一子会话，'
                  '保留其历史上下文），除非该 agent 尚无记录：\n')
        footer = ('\n不要为这些 agent 新建全新会话重复已完成的工作。'
                  '对同一 agent 的多次委派必须严格串行：上一次委派完全结束后才能发起'
                  '下一次（并行委派会绕过会话复用机制，导致上下文丢失）。\n\n')
        return header + '\n'.join(lines) + footer

    def _audit_reuse_violations(self, session_id: str, batch_id: str | None,
                                agents: list):
        """事后校验（可观测层）：复用名单内的 agent 若在父会话下出现多个
        子会话（插件未生效/静默分叉），记 warning + 批事件，不改变任务状态。"""
        if not agents:
            return
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT agent, count(*) FROM ai_chat_subtasks "
                        "WHERE root_session_id = %s AND agent = ANY(%s) "
                        "GROUP BY agent HAVING count(*) > 1",
                        (session_id, list(agents)),
                    )
                    viol = cur.fetchall()
            for agent, n in viol:
                logger.warning(
                    'subagent session reuse violated sid=%s agent=%s sessions=%d '
                    '(plugin enforcement may be inactive — OC serve restart needed '
                    'after first deploy)', session_id, agent, n)
                if batch_id:
                    from ai.batch import batch_events as batch_events
                    batch_events.append_event(
                        batch_id, 'child.reuse.violation',
                        aggregate_type='child', aggregate_id=session_id,
                        payload={'agent': agent, 'sessions': n})
        except Exception:  # noqa: BLE001
            logger.debug('reuse audit failed sid=%s', session_id, exc_info=True)

    @staticmethod
    def _check_agent(agent, directory):
        """Return an error string if `agent` can't be used as the session's
        primary agent (unknown, or a subagent), else None. Empty agent (default)
        is always OK. If OpenCode can't be queried we don't block (return None) —
        create_session will surface a real connectivity error instead."""
        agent = (agent or '').strip()
        if not agent:
            return None
        try:
            agents = opencode_client.list_agents(directory=directory) or []
            names = {a.get('name') for a in agents}
            primary = {a.get('name') for a in agents if a.get('mode') == 'primary'}
        except Exception:
            return None
        if agent in primary:
            return None
        if agent in names:
            return (f'Agent「{agent}」是 subagent，不能作为批任务的主 Agent。'
                    f'请改用 primary Agent，或在 prompt 里用 @{agent} 调用它。')
        return (f'Agent「{agent}」不存在（OpenCode 未找到）。'
                f'请确认该 Agent 已在 OpenCode 安装，或通过「预置仓库」提供给子任务。')


__all__ = [
    'logger',
    'get_db',
]
