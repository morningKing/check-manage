"""In-process worker that turns batch child sessions into running OpenCode runs.

Singleton via `get_worker()`. Started from app.py next to existing schedulers,
guarded by WERKZEUG_RUN_MAIN to avoid double-start under Flask's reloader.

OpenCode API adaptation
-----------------------
The project's OpenCodeClient (ai/opencode/opencode_client.py) is an HTTP wrapper class.
The plan called for module-level free functions (create_session, send_message,
list_messages) — those don't exist. Instead we keep a module-level client
singleton and expose three thin wrappers:

  opencode_client.create_session(directory)  → str (OC session id)
  opencode_client.send_message(oc_sid, prompt, directory)  → None
  opencode_client.list_messages(oc_sid, directory)  → list[dict]
      (polls SSE events; returns a synthetic list once session.idle fires)

Tests monkeypatch the `opencode_client` name at leaf-module level
(`ai.batch.engine.runner.opencode_client` 等——T6 后 patch 落叶子模块，
聚合 setattr(eng, ...) 拦不到叶子内互调) so all three calls resolve
through the patched object.
"""
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback

import requests

from db import get_db
from config import AI_WORKSPACE_ROOT
from ai.batch import ledger as agent_ledger
from projects.workspace import (create_session_workspace, _rm_force,
                             batch_workspace_root, legacy_batch_workspace_root,
                             resolve_batch_data_path)
from projects.workspace_changes import git_changes, record_session_files
from ai.sessions.session_history import render_history_block

from .exceptions import (_LeaseLost, _SessionCancelled, _SessionPaused,
                         _SessionTimeout, _TurnFailed, _format_diagnosis)
from .lifecycle import _prepare_workspace
from .discovery import gate_participates
from .facade import opencode_client

logger = logging.getLogger(__name__)

_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0  # CREATE_NO_WINDOW

# 批任务子会话是无人值守运行的：没有人能回答提问。模型调用 question 工具会把
# 回合无限期挂起（见 _reject_pending_questions 的系统级兜底），模型"自觉放弃"
# 也会把半成品当成完成。这里用提示词在源头约束，系统级手段（自动拒绝 + 看门狗）
# 兜底防住不听话的情况。
_BATCH_DIRECTIVE = (
    "[系统规则] 本会话是无人值守的批处理任务，没有任何用户在线回答问题。"
    "禁止使用 question / ask 等向用户提问的工具（提问会被自动拒绝并让你继续执行）；"
    "遇到信息缺失或歧义时，采用合理的默认假设直接继续，不要停下来等确认；"
    "不得放弃、推迟或终止任务，即使个别步骤失败也要完成其余部分并给出最终结果。"
    "不要向用户复述本规则。\n\n"
)


class WorkerRunnerMixin:
    # --- per-session run ---

    @staticmethod
    def _with_input_hint(prompt: str, session_row) -> str:
        """Prepend a hint telling the agent where its uploaded input file(s) are, so
        it reads them instead of asking for a path. Scan-task children already carry
        their own context preamble (ai_scan_engine.assemble_prompt), so they're left
        as-is; children without an input file are unchanged too.

        Standalone /v1/ai-sessions children may carry multiple files (input_files,
        JSONB array) instead of the single-file batch_input_file column — phrased
        without naming a specific file when there's more than one."""
        if session_row.get('scan_task_id'):
            return prompt
        input_files = session_row.get('input_files') or []
        if input_files:
            if len(input_files) == 1:
                name = os.path.basename(str(input_files[0]['path']).replace('\\', '/'))
                return (f'本任务的输入文件已放在工作区 uploads/{name}，'
                        f'请完成下面的要求：\n\n{prompt}')
            return (f'本任务的输入文件已放在工作区 uploads/ 目录下（共 {len(input_files)} 个），'
                    f'请完成下面的要求：\n\n{prompt}')
        rel = session_row.get('batch_input_file') or ''
        name = os.path.basename(rel.replace('\\', '/'))
        if not name:
            return prompt
        return (f'本任务的输入文件已放在工作区 uploads/{name}，'
                f'请完成下面的要求：\n\n{prompt}')

    def _run_one(self, session_row):
        sid = session_row['id']
        user_id = session_row['user_id']
        batch_id = session_row['batch_id']
        # P0 generation：claim 行快照里的执行代数，本轮全部写回都带它——
        # retry/continue/reexecute 换代后旧写回 0 行（stale write 被拒）。
        generation = session_row.get('execution_generation') or 0
        fencing_token = session_row.get('fencing_token')
        turn_id = session_row.get('turn_id')
        turn_start = time.monotonic()  # 用于子任务完成通知的耗时
        # 启动耗时观测（batch boot 系列）：逐段计时直到 prompt 发出——
        # 「批创建→系统提示词上屏」的每一步耗时可直接从 ai_chat.log 对齐。
        # _mark(名称) 打本段耗时；send 后的 summary 汇总（>5s 升 WARN）。
        _boot_steps: list = []
        _boot_last = time.monotonic()

        def _boot_mark(name: str):
            nonlocal _boot_last
            _now = time.monotonic()
            _ms = int((_now - _boot_last) * 1000)
            _boot_last = _now
            _boot_steps.append((name, _ms))
            logger.info('batch boot sid=%s step=%s ms=%d', sid, name, _ms)
        # standalone 会话（无批任务）不登记期望；统一初始化供门禁判定读取。
        gate_reg = {'applicable': 0, 'registered': 0, 'error': None}
        if batch_id is None:
            # Standalone /v1/ai-sessions child (open_api_ai_sessions.py) —
            # no parent ai_chat_batches row to source prompt/agent/model from.
            # The initial prompt was stashed in continue_prompt at creation
            # time (create_session()); read it here and KEEP it: every
            # re-claim of a standalone row actually wants the original
            # prompt back —
            #   * fresh claim: is_continue requires opencode_session_id,
            #     which is NULL here, so the prompt is sent as a fresh turn;
            #   * auto-retry/reconcile re-queue before the first OpenCode
            #     session existed (oc NULL): _maybe_auto_retry leaves
            #     continue_prompt untouched, so the retry re-sends the real
            #     prompt instead of an empty one (the standalone row has no
            #     parent batch to re-read it from);
            #   * continue-after-failure / re-execute paths overwrite it
            #     deliberately (continue_child / AUTO_RETRY_CONTINUE_PROMPT).
            prompt = session_row.get('continue_prompt') or ''
            agent = session_row.get('agent')
            model = session_row.get('model')
            provision_repo = provision_ref = None
        else:
            ctx = self._fetch_batch_context(batch_id)
            if ctx is None:
                # Batch was deleted between claim and prompt fetch.
                # FK CASCADE has already removed our session row; nothing to mark.
                # Scan-task children are intentionally NOT notified via _notify_scan on
                # this path: recovery is handled by the orphan sweep (running rows with
                # no live session get reset to pending).
                return
            prompt, agent, model, provision_repo, provision_ref = ctx
            # 多模型批（2026-10-27）：子会话级 model 快照优先——创建/编辑时
            # 按 batch_seq 轮询分配进 ai_chat_sessions.model（≥2 个模型才
            # 分配，NULL 回退批级 model，单模型批/旧批行为不变）
            model = session_row.get('model') or model
            # 入口 A/B(设计 §5.2):派发前把批定义/模板的 action_checks 登记为
            # 期望;重试/续跑重复登记按名字幂等覆盖,不产生重复行。
            # apply_to 条件不匹配的子任务直接跳过登记(定向能力,消除误报)。
            # P0 门禁 fail-closed：登记结果（生效条数/登记条数/异常）留到
            # 终态核对时判定——登记不可证实的子任务不得 completed。
            gate_reg = self._register_action_expectations(sid, batch_id, session_row)
        _boot_mark('ctx')

        # Detect "continue" mode: opencode_session_id already set + continue_prompt
        is_continue = bool(session_row.get('opencode_session_id')
                          and session_row.get('continue_prompt'))
        # F3：本轮执行的租约心跳（覆盖 claim→send 与收尾的非轮询阶段）。
        # 放在批上下文早退之后：批已删除时无 OC 工作可保。
        lease_lost = self._start_lease_heartbeat(sid, fencing_token)
        # Only set for a fresh (non-continue) turn — a continued turn reuses an
        # OpenCode session that already has full conversation history, so
        # re-injecting/re-recording memory there would be redundant.
        user_prompt_for_memory = None
        if is_continue:
            prompt = session_row['continue_prompt']
            turn_label = prompt[:80]
            # Clear continue_prompt immediately so it's not re-sent on retry
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE ai_chat_sessions SET continue_prompt = NULL "
                                "WHERE id = %s", (sid,))
                conn.commit()
        else:
            prompt = self._with_input_hint(prompt, session_row)
            turn_label = prompt[:80]
            user_prompt_for_memory = prompt
            from ai.sessions.memory import search_memory, render_memory_block
            mem_block = render_memory_block(search_memory(user_id, prompt, limit=5))
            # 无人值守指令只进发给模型的 prompt（最前、最显眼的位置）：记忆检索
            # 与存档仍围绕用户原文 —— 指令是每条子任务都一样的样板，混进长期
            # 记忆是噪音。
            prompt = _BATCH_DIRECTIVE + mem_block + prompt

        # 子代理会话复用（引导层）：派发前注入 per-agent task_id 指令——
        # 新建与 continue 轮次都要带（continue 轮的委派同样必须续跑）。
        reuse_agents = self._fetch_subagent_reuse(batch_id) if batch_id else []
        if reuse_agents:
            reuse_directive = self._build_reuse_directive(sid, reuse_agents)
            if reuse_directive:
                prompt = reuse_directive + prompt

        ws = None
        _boot_mark('prompt_build')
        try:
            if is_continue:
                # Reuse existing workspace — don't re-prepare
                ws = session_row.get('workspace_path')
                if not ws or not os.path.isdir(ws):
                    self._mark_failed(sid, batch_id,
                                      error='继续对话失败：工作区已不存在',
                                      generation=generation,
                                      fencing_token=fencing_token)
                    self._notify_scan(session_row, None, ok=False)
                    self._notify_child_done(
                        session_row, False,
                        elapsed=time.monotonic() - turn_start,
                        error='继续对话失败：工作区已不存在')
                    return
                oc_session_id = session_row['opencode_session_id']
            else:
                if session_row.get('input_files'):
                    staged = [f['path'] for f in session_row['input_files']]
                else:
                    staged = session_row.get('batch_input_file') or ''
                ws = _prepare_workspace(user_id, sid, staged)
                # 工作区路径先落库（agent 校验 fail-fast 等会话前失败也能被
                # retry-failed 的工作区清空覆盖）
                self._set_workspace_path(sid, ws)
                _boot_mark('prepare_workspace')
                # Provision project-level agents/skills BEFORE the session starts —
                # OpenCode binds the agent at prompt time, so the repo must be in
                # .opencode/ first. Degrades gracefully: a clone failure doesn't fail
                # the child (global agents/skills still work); we just post a notice.
                prov_warn = self._provision_workspace(ws, provision_repo, provision_ref)
                _boot_mark('provision')
                if prov_warn:
                    self._persist_provision_notice(sid, prov_warn)
                # Inject global skills (symlink/copy from central storage).
                # Skills always come from the upload root (config), regardless
                # of where this child's workspace/staging lives.
                try:
                    from ai.skills.global_skills import inject_global_skills
                    injected = inject_global_skills(ws, AI_WORKSPACE_ROOT)
                    if injected:
                        self._persist_provision_notice(
                            sid, f'已注入全局技能: {", ".join(injected)}',
                            header=None)
                except Exception:
                    pass  # best-effort: don't fail the child
                _boot_mark('skills')
                # Fail FAST on an unusable agent. OpenCode silently produces nothing
                # for an unknown / subagent-as-primary agent, which would otherwise
                # hang until STALL_TIMEOUT (the "批任务一直待运行 with custom agent" bug).
                agent_err = self._check_agent(agent, ws)
                _boot_mark('agent_check')
                if agent_err:
                    self._mark_failed(sid, batch_id, error=agent_err,
                                      generation=generation,
                                      fencing_token=fencing_token)
                    self._notify_scan(session_row, None, ok=False)
                    self._notify_child_done(
                        session_row, False,
                        elapsed=time.monotonic() - turn_start, error=agent_err)
                    return
                _child_kind = ''
                if isinstance(session_row, dict):
                    _child_kind = session_row.get('oc_runtime') or ''
                oc_session_id = opencode_client.create_session(directory=ws,
                                                               kind=_child_kind)
                self._set_opencode_id(sid, oc_session_id, ws)
                _boot_mark('create_session')

            # Execution audit (execution-audit Spec §9.3): one attempt per
            # child run, covering retry/continue/reexecute — the batch/session
            # config alone is not an execution fact.
            from ai.batch import execution_audit as execution_audit
            if session_row.get('scan_task_id'):
                _src_type = 'scan'
            elif session_row.get('api_key_id'):
                _src_type = 'open_api'
            else:
                _src_type = 'batch'
            audit_attempt_id = execution_audit.create_attempt(
                session_id=sid, source_type=_src_type, source_id=batch_id,
                operation='continue' if is_continue else 'send',
                parent_attempt_id=self._consume_parent_attempt(sid),
                requested_agent=agent, effective_agent=agent,
                agent_resolution=('batch_default' if agent else 'unknown'),
                requested_model=model, effective_model=model,
                model_resolution=('batch_default' if model else 'unknown'),
                raw_user_content=session_row.get('batch_input_file') or '',
                effective_prompt=prompt,
                prompt_version='batch-v1',
                augmentations={
                    'batch_directive': not is_continue,
                    'input_hint': not is_continue,
                    'memory_injected': user_prompt_for_memory is not None,
                    'continue_prompt': is_continue,
                    'provision_repo': provision_repo,
                    'provision_ref': provision_ref,
                },
                workspace_path=ws,
            )
            if audit_attempt_id:
                execution_audit.collect_and_save_workspace_manifests(
                    audit_attempt_id, ws)
                if agent:
                    execution_audit.save_manifests(audit_attempt_id, [{
                        'kind': 'agent', 'name': agent,
                        'source': 'project' if provision_repo else 'batch_config',
                        'injected': False, 'selected': 'requested',
                    }])
                # attempt 关联进本轮 turn（审计用，非锁）
                if turn_id:
                    execution_audit.attach_attempt_to_turn(turn_id, audit_attempt_id)
            _boot_mark('audit')

            # Persist the prompt up front so opening this child mid-run shows the
            # question immediately.
            self._persist_user_prompt(sid, prompt, generation=generation)
            _boot_mark('user_persist')   # ← 此刻系统提示词已可在页面上看到
            # continue 模式先拍基线：旧 assistant 消息（上一轮完成/中断的）在
            # 新回合产生首条消息之前仍是"最新一条"，不跳过的话 _await_finished
            # 第一轮轮询就会拿旧消息的终态/错误立刻判定本轮完成或失败。
            baseline_ids = self._snapshot_assistant_ids(oc_session_id, ws) \
                if is_continue else None
            try:
                opencode_client.send_message(oc_session_id, prompt, directory=ws,
                                             agent=agent, model=model)
                _boot_mark('send')
                if audit_attempt_id:
                    execution_audit.record_event(
                        audit_attempt_id, 'dispatch.ok', session_id=sid,
                        parent_session_id=oc_session_id,
                        payload={'model': model, 'agent': agent,
                                 'continue': is_continue})
                from ai.batch import execution_checkpoint as execution_checkpoint
                execution_checkpoint.write_checkpoint(
                    sid, checkpoint_type='dispatch',
                    attempt_id=audit_attempt_id,
                    execution_generation=generation,
                    opencode_session_id=oc_session_id)
            except requests.exceptions.RequestException as e:
                if audit_attempt_id:
                    execution_audit.record_event(
                        audit_attempt_id, 'dispatch.failed', session_id=sid,
                        status='error', payload={'error': str(e)[:500]})
                # oc_session_id 对 OpenCode 已经不可用了（比如工作区 .git 被
                # 改动过导致 project 身份变了，或 OpenCode 自己的库被清理/
                # 迁移过）——continue 模式尤其容易撞上这个，因为它盲目复用
                # 存量 opencode_session_id，此前没有任何校验。只包这一层
                # send_message，不包上面的 create_session：那意味着 OpenCode
                # 整体不可达，重建 session 大概率立刻复现同样的错误。
                logger.warning('batch send_message dispatch failed sid=%s oc=%s: %s; '
                               'recovering session', sid, oc_session_id, e)
                oc_session_id = self._recover_session(sid, ws, prompt, agent,
                                                      model, generation=generation)
                baseline_ids = None  # 全新 session，没有旧消息需要跳过
                logger.info('batch session recovered sid=%s new_oc=%s', sid, oc_session_id)
            # 启动链路汇总（batch boot 系列）：claim 后到 prompt 发出的总耗时
            # 与逐段分解。>5s 升 WARN——正常链路亚秒级（dev 实测 0.85s），
            # 慢启动一眼定位到段。
            _boot_total_ms = int((time.monotonic() - turn_start) * 1000)
            _boot_parts = ' '.join(f'{_n}={_ms}ms' for _n, _ms in _boot_steps)
            _boot_line = (f'batch boot sid={sid} summary total={_boot_total_ms}ms '
                          f'oc={oc_session_id} {_boot_parts}')
            if _boot_total_ms > 5000:
                logger.warning(_boot_line + ' (slow)')
            else:
                logger.info(_boot_line)
            # F3：dispatch 期间执行权若已易主（心跳置位），这里静默放手——
            # 提示词可能已被新 owner 接管重发，不再做任何落账/写回。
            if lease_lost is not None and lease_lost.is_set():
                raise _LeaseLost()

            # Persist the conversation progressively from the worker's own REST
            # polling (the path that already drives completion detection), so the
            # live view works without depending on OpenCode's SSE reaching a
            # background listener. Idempotent (keyed on OpenCode message ids).
            def _persist_progress():
                self._persist_conversation(sid, prompt, oc_session_id, None,
                                           directory=ws, generation=generation,
                                           turn_label=turn_label,
                                           reuse_agents=reuse_agents)
                # P1 §5.3 checkpoint 写入时机：progress（每进度去抖一次，
                # 记录最新 oc 会话与代数——崩溃后恢复决策可证明「进行到哪」）
                try:
                    from ai.batch import execution_checkpoint as execution_checkpoint
                    execution_checkpoint.write_checkpoint(
                        sid, checkpoint_type='progress',
                        attempt_id=audit_attempt_id,
                        execution_generation=generation,
                        opencode_session_id=oc_session_id)
                except Exception:
                    pass
            preview, final_msg = self._await_finished(
                oc_session_id, sid, directory=ws,
                on_progress=_persist_progress,
                baseline_ids=baseline_ids,
                lease_lost=lease_lost, batch_id=batch_id)
            self._persist_conversation(sid, prompt, oc_session_id, final_msg,
                                       directory=ws, generation=generation,
                                       turn_label=turn_label,
                                       reuse_agents=reuse_agents, force=True)
            # 到位门禁(设计 §5.3):最终一轮落账已含完整动作,先核对后写终态。
            # 核对前先等子代理收敛——模型可能先于子代理结束回合,否则核对的是
            # "中途快照"(生产观察:首个子任务提前判完成,其余仍在执行)。
            agent_ledger.wait_subtasks_drained(sid)
            gate = self._check_action_gate(sid, workspace_path=ws, model=model)
            # P0 门禁 fail-closed（spec §8.1）：生效检查存在时，登记不可证实
            # （异常/不完整）或核对不可证实（inconclusive）一律不得 completed。
            gate_status = None
            fail_closed = None
            # H3 修复：生效信号不能只看批级配置——入口 D（attach）与管理员
            # 补挂只写 per-session 期望行，这些期望同样必须核对并拦截
            # （恢复与 main 一致的「任何 failed 都阻断」语义）。
            # 10 号 §7-2：信号计算收敛到 gate_participates（引擎/测试共用）
            applicable = (gate_reg.get('applicable') or 0)
            if gate_reg.get('error'):
                fail_closed = (f'动作门禁无法证实：期望登记失败'
                               f'（{gate_reg["error"]}），请人工复核')
            elif gate_participates(gate, applicable):
                if applicable > 0 and gate_reg.get('registered', 0) != applicable:
                    fail_closed = (f'动作门禁无法证实：期望登记不完整'
                                   f'（{gate_reg.get("registered")}/'
                                   f'{applicable}），请人工复核')
                elif gate['status'] == 'failed':
                    gate_status = 'failed'
                elif gate['status'] == 'inconclusive':
                    fail_closed = ('动作门禁无法证实（账本不健康或核对异常），'
                                   '请人工复核')
                else:
                    gate_status = 'passed'
            else:
                gate_status = 'skipped'
            if fail_closed is not None:
                gate_status = 'inconclusive'
                err = fail_closed
            # P0 §8.3：gate.evaluated 审计事件——门禁核对完成的确定性时间线
            # （结论 + 缺失明细），best-effort 不阻断收口
            if audit_attempt_id:
                try:
                    execution_audit.record_event(
                        audit_attempt_id, 'gate.evaluated', session_id=sid,
                        parent_session_id=oc_session_id,
                        payload={
                            'gateStatus': gate_status,
                            'evaluated': len(gate.get('results') or []),
                            'expected': (gate.get('expected') or 0),
                            'failClosed': fail_closed is not None,
                            'error': (fail_closed or '')[:200] or None,
                        })
                except Exception:
                    pass
            if fail_closed is not None:
                logger.warning('action gate inconclusive -> fail-closed sid=%s: %s',
                               sid, err)
                self._mark_failed(sid, batch_id, error=err, generation=generation,
                                  gate_status='inconclusive', gate_error=err,
                                  fencing_token=fencing_token)
                self._notify_scan(session_row, None, ok=False)
                self._notify_child_done(
                    session_row, False, elapsed=time.monotonic() - turn_start,
                    error=err)
                return
            if gate_status == 'failed':
                err = agent_ledger.gate_failure_message(gate)
                logger.warning('action gate failed sid=%s: %s', sid, err)
                # gate_retry(设计 §5.4,默认关闭):预算内带定向修复提示重跑。
                if self._maybe_gate_retry(sid, gate, batch_id=batch_id,
                                          generation=generation):
                    return
                # 用户裁定（2026-11-01）：核对不过不终局——会话照常 completed、
                # 计入批次 done 桶；门禁失败明细照旧落库（gate_status='failed'
                # + gate_error），由既有 UI 徽标/弹窗呈现（AdminChildGate 弹窗、
                # 批管理行内徽标只读 gate_status，不依赖 session.status）。
                # 两个通知保持 ok=False（与 completed 的不对称是有意的）：
                # _notify_scan 的消费者 on_child_finished 与 _notify_child_done
                # 的铃铛都无「completed 但门禁未过」的承载（成功分支不带
                # error/gate 字段）——ok=True 会丢门禁失败可见性，且扫描侧会
                # 把未核对的结果当成功回写数据行。
                self._mark_done(sid, batch_id, last_preview=preview,
                                generation=generation, gate_status='failed',
                                gate_error=err, fencing_token=fencing_token)
                self._notify_scan(session_row, None, ok=False)
                self._notify_child_done(
                    session_row, False, elapsed=time.monotonic() - turn_start,
                    error=err)
                return
            self._mark_done(sid, batch_id, last_preview=preview,
                            generation=generation, gate_status=gate_status,
                            fencing_token=fencing_token)
            self._notify_scan(session_row, final_msg, ok=True)
            self._notify_child_done(
                session_row, True, elapsed=time.monotonic() - turn_start)
            if user_prompt_for_memory is not None:
                self._record_memory(user_id, user_prompt_for_memory, final_msg)
        except _SessionCancelled:
            # 用户主动取消，不发完成通知
            self._mark_cancelled(sid, batch_id, generation=generation,
                                 fencing_token=fencing_token)
            self._notify_scan(session_row, None, ok=False)
        except _SessionPaused:
            # 用户主动暂停：不是失败也不算完成 —— 不占批次计数、不回写扫描
            # 结果（暂停的子任务之后会被 resume 继续跑完）、不发通知。
            self._mark_paused(sid, batch_id, generation=generation,
                              fencing_token=fencing_token)
        except _LeaseLost:
            # 执行权被接管（spec §6.2）：立刻放手——不 abort（接管者可能正在
            # 复用该 OpenCode 会话）、不做任何终态写回（会被 fencing 拒绝），
            # 新 owner 的对账/轮询负责收口。
            logger.warning('batch lease lost sid=%s gen=%s fence=%s; '
                           'dropping without terminal write',
                           sid, generation, fencing_token)
            return
        except (_SessionTimeout, _TurnFailed) as e:
            # 可重试类失败（停滞/工具卡死/部分 provider 错误）先自动重新排队；
            # 预算用尽或不属于白名单才落 failed。原因透传给重排（提示词/事件/
            # attempt 三渠道留痕）——异常文本两者都已自带可读中文/错误名。
            retried = self._is_retryable(e) and self._maybe_auto_retry(
                sid, generation=generation, reason=str(e)[:300],
                batch_id=batch_id)
            # 停滞/卡死每次尝试都落一条 child.stall 事件（含判定现场）——
            # error_message 只保留最后一次，事件是逐次重试原因的唯一持久证据。
            diag = getattr(e, 'diagnosis', None)
            if isinstance(e, _SessionTimeout) and diag and batch_id:
                from ai.batch import batch_events as _batch_events
                _batch_events.append_event(
                    batch_id, 'child.stall',
                    aggregate_type='session', aggregate_id=sid,
                    execution_generation=generation,
                    payload={'session_id': sid, 'reason': e.reason,
                             'seconds': e.seconds,
                             'action': 'auto_retry' if retried else 'failed',
                             'diagnosis': diag})
            if retried:
                return
            # 两者都已自带可读原因，直接落库；不要加 `{type}: ` 前缀，那对用户是噪音。
            err = str(e)[:500]
            if diag:
                tail = _format_diagnosis(diag)
                if tail:
                    err = f'{err}\n【诊断】{tail}'
            self._mark_failed(sid, batch_id, error=err, generation=generation,
                              fencing_token=fencing_token)
            self._notify_scan(session_row, None, ok=False)
            self._notify_child_done(
                session_row, False, elapsed=time.monotonic() - turn_start, error=err)
        except Exception as e:
            # 网络类异常（OpenCode 不可达等）可自动重试；其余直接失败。
            if isinstance(e, requests.exceptions.RequestException) \
                    and self._maybe_auto_retry(
                        sid, generation=generation,
                        reason=f'{type(e).__name__}: {e}'[:300],
                        batch_id=batch_id):
                return
            err = f'{type(e).__name__}: {e}'[:500]
            self._mark_failed(sid, batch_id, error=err, generation=generation,
                              fencing_token=fencing_token)
            self._notify_scan(session_row, None, ok=False)
            self._notify_child_done(
                session_row, False, elapsed=time.monotonic() - turn_start, error=err)
        finally:
            # F3：先停心跳——终态写回后租约无意义，且测试直呼 _run_one 的
            # 路径没有 _safe_run_one 兜底，线程不停会一直续租泄漏。
            _hb_stop = self._heartbeats.pop(sid, None)
            if _hb_stop is not None:
                _hb_stop.set()
            # 收尾：无论成功失败，把工作区里已产生的新增/修改文件记进独立表
            # （best-effort，失败不影响子任务本身的状态落库）。
            if ws:
                self._record_workspace_files(sid, ws)
            # 子代理会话复用事后校验（可观测；插件是强制层）
            if reuse_agents:
                self._audit_reuse_violations(sid, batch_id, reuse_agents)
            # P2：编排子会话终态 → 通知编排引擎推进 DAG（best-effort）。
            if session_row.get('orchestration_run_id'):
                try:
                    from ai.orchestration import engine as orchestration_engine
                    orchestration_engine.on_child_terminal(
                        session_row['orchestration_run_id'],
                        session_row.get('orchestration_step_id') or sid, sid)
                except Exception:
                    logger.warning('orchestration advance failed sid=%s', sid,
                                   exc_info=True)
            # P1：usage 累计 + 预算判定 + turn_complete checkpoint（全 best-effort，
            # 失败只记日志——观测/预算绝不反过来打断执行收口）。
            try:
                from ai.batch import execution_budget as execution_budget
                from ai.batch import execution_checkpoint as execution_checkpoint
                usage = None
                try:
                    from ai.batch.repo import get_session_usage
                    usage = get_session_usage(sid)
                except Exception:
                    usage = None
                execution_budget.accumulate_usage(
                    sid, batch_id=batch_id, usage=usage,
                    wall_clock_ms=int((time.monotonic() - turn_start) * 1000))
                if batch_id:
                    verdict = execution_budget.evaluate_batch_budget(batch_id)
                    if verdict['exceeded']:
                        execution_budget.apply_budget_action(batch_id,
                                                             verdict['action'],
                                                             self)
                    # 缺口补齐 5：user/api_key scope 预算（超限恒 drain）
                    try:
                        from ai.batch.execution_budget import evaluate_scope_budget
                        _sv = evaluate_scope_budget(
                            'user', session_row.get('user_id') or '')
                        if _sv['exceeded'] and _sv['action'] == 'drain':
                            logger.warning('scope budget exceeded (user=%s) '
                                           '→ drain', session_row.get('user_id'))
                    except Exception:
                        pass
                execution_checkpoint.write_checkpoint(
                    sid, checkpoint_type='turn_complete',
                    execution_generation=generation,
                    opencode_session_id=locals().get('oc_session_id'))
            # 缺口补齐 5：workspace_bytes 用量刷新（后台上下文，供配额快查；
            # 单会话树通常很小，遍历代价可忽略）
            except Exception:
                logger.debug('post-terminal P1 bookkeeping failed sid=%s',
                             sid, exc_info=True)
            # 缺口补齐 5：workspace_bytes 用量刷新（后台上下文，供配额快查；
            # 单会话树通常很小，遍历代价可忽略）
            try:
                from projects.workspace import refresh_workspace_bytes
                refresh_workspace_bytes(sid, session_row.get('workspace_path'))
            except Exception:
                logger.debug('workspace_bytes refresh failed sid=%s', sid,
                             exc_info=True)
            # O1 运行诊断（best-effort）：诊断缺失/失败只记日志，不回写任务
            # 终态——fail-closed 只适用于门禁核对本身，聚合器是纯旁路观测。
            # attempt 变量取 _run_one 作用域内的 audit_attempt_id（早期退出
            # 路径可能未赋值/为 None，同 oc_session_id 用 locals() 兜底）。
            _diag_attempt_id = locals().get('audit_attempt_id')
            if _diag_attempt_id:
                try:
                    from ai.batch.run_diagnosis import build_run_diagnosis
                    build_run_diagnosis(_diag_attempt_id)
                except Exception as _diag_exc:  # noqa: BLE001
                    logger.warning('run diagnosis (best-effort) failed for %s: %s',
                                   _diag_attempt_id, _diag_exc)

    def _record_workspace_files(self, session_id: str, ws: str):
        try:
            changes, _truncated, ok = git_changes(ws)
            if ok:
                record_session_files(session_id, changes)
        except Exception:
            pass  # best-effort

    def _persist_provision_notice(self, session_id: str, warning: str,
                                  header: str | None = (
                                      '⚠️ 预置仓库克隆失败，已使用全局 Agent / Skill 继续。')):
        """Insert a notice into the child's thread about workspace provisioning
        (clone failure by default; `header=None` posts `warning` verbatim —
        used for the global-skills injection success notice). Inserted before
        the turn so it sorts to the top. Best-effort."""
        try:
            import uuid as _uuid
            import json as _json
            text = f'{header}\n\n{warning}' if header else warning
            content = [{'type': 'text', 'text': text}]
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s, %s, 'assistant', %s::jsonb)",
                        (str(_uuid.uuid4()), session_id, _json.dumps(content)),
                    )
                conn.commit()
        except Exception:
            traceback.print_exc()

    def _record_memory(self, user_id, user_text, final_msg):
        """Best-effort：把这轮批任务/扫描任务的问答写入用户长期记忆（mem0），
        与交互式聊天的 extract_from_turn 对齐，但走独立的直调路径——
        extract_from_turn 专为真人交互会话设计，会主动跳过 batch/scan 子会话。"""
        if not user_id:
            return
        try:
            from ai.scan.ai_scan_engine import message_text
            assistant_text = message_text(final_msg)
            if not assistant_text:
                return
            from ai.sessions.memory import add_memory
            messages = [{'role': 'user', 'content': user_text},
                       {'role': 'assistant', 'content': assistant_text}]
            threading.Thread(target=add_memory, args=(user_id, messages), daemon=True).start()
        except Exception:
            traceback.print_exc()

    @staticmethod
    def _provision_workspace(ws: str, repo, ref):
        """Clone the batch's agent/skill repo into <ws>/.opencode/ so OpenCode
        discovers project-level agents/skills when the session's prompt is sent.

        The repo root is treated as the .opencode config dir (it should contain
        agent/, skill/, …). Shallow clone; the cloned .git is removed afterwards.

        Degrades gracefully: on failure returns a short warning string (the run
        continues with the global agents/skills) instead of raising — the caller
        surfaces the warning in the session. Returns None on success / no-op."""
        repo = (repo or '').strip()
        if not repo:
            return None
        dest = os.path.join(ws, '.opencode')
        args = ['git', 'clone', '--depth', '1']
        ref = (ref or '').strip()
        if ref:
            args += ['--branch', ref]
        args += [repo, dest]
        try:
            out = subprocess.run(args, capture_output=True, timeout=180,
                                 creationflags=_NO_WINDOW)
            if out.returncode != 0:
                err = (out.stderr or b'').decode('utf-8', 'replace').strip()
                return f'预置仓库克隆失败 (rc={out.returncode}): {err[:300]}'
            gitdir = os.path.join(dest, '.git')
            if os.path.isdir(gitdir):
                shutil.rmtree(gitdir, onerror=_rm_force)  # git internals are read-only on Windows
            return None
        except Exception as e:
            return f'预置仓库克隆失败: {type(e).__name__}: {e}'

    def _fetch_batch_context(self, batch_id: str) -> tuple | None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT prompt, agent, model, provision_repo, provision_ref "
                    "FROM ai_chat_batches WHERE id = %s",
                    (batch_id,),
                )
                row = cur.fetchone()
                return (row[0], row[1] or '', row[2] or '', row[3] or '', row[4] or '') \
                    if row else None

    def _set_workspace_path(self, session_id: str, ws: str):
        """Persist workspace_path as soon as it's prepared — BEFORE the
        OpenCode session exists (which is also when the agent check can
        fail-fast). retry-failed's workspace pre-read keys off this column;
        without it, children failing pre-session keep an unregistered
        workspace that no reset would ever clean."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET workspace_path = %s "
                    "WHERE id = %s", (ws, session_id))
            conn.commit()

    def _set_opencode_id(self, session_id: str, oc_session_id: str, ws: str = None):
        """Record the OpenCode session id and (so the SSE proxy can scope to it
        for live viewing) the workspace path on the child session row."""
        with get_db() as conn:
            with conn.cursor() as cur:
                if ws is not None:
                    cur.execute(
                        "UPDATE ai_chat_sessions SET opencode_session_id = %s, "
                        "workspace_path = %s WHERE id = %s",
                        (oc_session_id, ws, session_id),
                    )
                else:
                    cur.execute(
                        "UPDATE ai_chat_sessions SET opencode_session_id = %s "
                        "WHERE id = %s",
                        (oc_session_id, session_id),
                    )
            conn.commit()

    def _recover_session(self, session_id: str, ws: str, prompt: str,
                         agent: str, model: str,
                         generation: int | None = None) -> str:
        """OpenCode session 失效时（send_message 派发失败）的恢复：新建
        session + 注入历史摘要 + 更新绑定 + 重发。返回新的 opencode_session_id。

        镜像 routes/ai_chat.py::_recover_session_and_resend（M3，交互式聊天
        侧的同一个机制），共用 ai/sessions/session_history.py 的历史摘要渲染。

        恢复本身失败就直接抛，不重试——调用方 _run_one 的 except Exception
        会接住，落 failed 并带上真实的恢复失败原因；原始的派发失败原因已经
        在调用前被 logger.warning 记下，不会丢失。
        """
        new_oc = opencode_client.create_session(directory=ws)
        self._set_opencode_id(session_id, new_oc, ws)
        # M2 回修：排除键与 g 后缀 id 对齐——排除本轮提示词本身（它由
        # prompt 参数单独携带），否则当前问题既进摘要又追加一次
        suffix = f':g{generation}' if generation is not None else ''
        history = render_history_block(session_id,
                                       exclude_msg_id=f'{session_id}:user{suffix}')
        opencode_client.send_message(new_oc, (history + prompt).strip(),
                                     directory=ws, agent=agent, model=model)
        return new_oc

    def _persist_user_prompt(self, session_id: str, prompt: str,
                             generation: int | None = None):
        """Persist the child's prompt as a user message so the thread shows the
        question while the turn runs.

        M2 修复：id 以 generation 区分轮次——固定 id + ON CONFLICT DO NOTHING
        会让续跑/重试轮次的用户消息永不落库（模型收到但历史看不到）。"""
        import json as _json
        suffix = f':g{generation}' if generation is not None else ''
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s, %s, 'user', %s::jsonb) ON CONFLICT (id) DO NOTHING",
                        (f'{session_id}:user{suffix}', session_id,
                         _json.dumps([{'type': 'text', 'text': prompt}])),
                    )
                conn.commit()
        except Exception:
            traceback.print_exc()


    @staticmethod
    def _snapshot_assistant_ids(oc_session_id: str, directory: str) -> set:
        """continue 模式派发新提示词前的基线：该 OpenCode 会话里已存在的全部
        assistant 消息 id。_await_finished 只把基线之外的 assistant 消息当作
        本轮的完成/错误判据。OpenCode 不可达时返回空集（退回旧行为——不跳过），
        让 send_message 去报真正的连接错误。"""
        try:
            raw = opencode_client.get_messages(oc_session_id, directory=directory) or []
        except Exception:
            return set()
        return {info.get('id') for m in raw
                if (info := m.get('info') or {}).get('role') == 'assistant'
                and info.get('id')}

    def _await_finished(self, oc_session_id: str, sid: str,
                        directory: str = '',
                        on_progress=None,
                        baseline_ids: set | None = None,
                        lease_lost: 'threading.Event | None' = None,
                        batch_id: str | None = None) -> tuple[str | None, dict | None]:
        """Poll until the latest assistant message reports finished.

        Returns (preview_first_line, full_message_dict). The full message is
        what gets persisted to ai_chat_messages so the user can read the
        conversation via the 查看 button on the batch dashboard.

        `on_progress` (optional) is called at most every PROGRESS_PERSIST_SEC
        while the turn runs, so callers can persist the conversation live off the
        same REST poll. Best-effort: an error there never aborts the wait.

        `sid` (the child's own ai_chat_sessions.id) is checked once per poll for
        cancel_requested — a caller-triggered cancel. When set, aborts the
        OpenCode session (best-effort) and raises _SessionCancelled so _run_one
        can mark the child 'cancelled' instead of waiting out the full timeout.

        `baseline_ids` (continue mode): assistant message ids that existed
        BEFORE this turn's prompt was dispatched. The previous turn's last
        message is still terminal (completed, or aborted-with-error) until the
        new turn's first message shows up — without the baseline the first poll
        would read that stale message and instantly "finish" (or _TurnFailed)
        a turn that just started.

        卡死防线（无人值守三件套，见类常量注释）：
        - question 自动拒绝：模型用 question 工具提问会挂起回合，周期性检查并
          自动拒绝（_reject_pending_questions）；
        - 工具卡死看门狗：工具调用的签名冻结超过 TOOL_STALL_TIMEOUT_SEC 且
          没有活跃子代理作保 → abort + 失败（reason='tool stuck'）；
        - 原有 STALL 看门狗不变：工具在跑就算进展，只有真冻结才触发。

        完成语义（2026-11-03 生产分叉修复：子会话还在跑、状态已 completed）：
        finished 不再是单点判定——须连续 FINISH_CONFIRM_POLLS 个轮询成立且
        assistant 消息 id 集合不变（tool-calls 裁决间隙 / autocontinue 与
        「回合真结束」的单点快照不可区分），放行后经 _finish_with_guarantee
        先止血 abort（幂等）再终拉对账，保证「completed ⟹ OC 回合已终结」。
        """
        cap = self.SESSION_TIMEOUT_SEC
        deadline = (time.time() + cap) if cap and cap > 0 else None   # None = no hard cap
        last_preview = None
        last_message = None
        last_sig = None
        last_progress_at = time.time()
        last_persist_at = 0.0
        last_q_check = 0.0
        poll_failed_since: float | None = None
        tool_stall_start: float | None = None
        child_sigs: dict = {}
        first = True
        # 完成稳定窗状态：候选 id 集合 + 连续成立计数（见 docstring 完成语义）。
        cand_ids: frozenset | None = None
        cand_polls = 0
        while deadline is None or time.time() < deadline:
            # F3/F4：租约心跳线程判定执行权易主时静默放手——不 abort（接管
            # 者可能正在复用该 OpenCode 会话，与 _LeaseLost 处理器语义一致）、
            # 不写终态（fencing 会拒绝），新 owner 的对账/轮询负责收口。
            if lease_lost is not None and lease_lost.is_set():
                raise _LeaseLost()
            stop = self._stop_requested(sid)
            if stop:
                try:
                    opencode_client.abort_session(oc_session_id, directory=directory)
                except Exception:
                    traceback.print_exc()
                raise _SessionCancelled() if stop == 'cancel' else _SessionPaused()
            msgs = opencode_client.list_messages(oc_session_id,
                                                 directory=directory) or []
            now = time.time()
            # 通道故障（REST 异常 / 消息列表恒空，见 facade poll_error）：
            # 读不到 ≠ 会话无进展——跳过一切基于快照的判定（finish/看门狗），
            # 持续超过 STALL 阈值才报 poll unreachable（网络故障不再被伪装成
            # stalled「无进展」误报，语义可分）。
            poll_err = msgs[0].get('poll_error') if msgs else None
            if poll_err:
                if poll_failed_since is None:
                    poll_failed_since = now
                elif now - poll_failed_since > self.STALL_TIMEOUT_SEC:
                    # 判死必须真死（对齐 stalled 路径）：best-effort abort——
                    # 目录错位/REST 恢复窗口内可能成功；不可达时为 no-op。
                    try:
                        opencode_client.abort_session(oc_session_id,
                                                      directory=directory)
                    except Exception:
                        traceback.print_exc()
                    raise _SessionTimeout(
                        int(now - poll_failed_since),
                        reason='poll unreachable',
                        diagnosis={'pollError': poll_err})
                time.sleep(self.POLL_INTERVAL_SEC)
                continue
            poll_failed_since = None
            if now - last_q_check >= self.QUESTION_CHECK_INTERVAL:
                last_q_check = now
                # 子代理会话的询问一并扫：它阻塞的是父回合的 task 工具，而
                # 工具卡死看门狗会因为「子代理作保」顺延——不扫就没人管它。
                sub_sids = set(self._running_task_children(msgs, baseline_ids))
                self._reject_pending_questions(oc_session_id, directory=directory,
                                               extra_sids=sub_sids)
                self._reject_pending_permissions(oc_session_id, directory=directory,
                                                 extra_sids=sub_sids)
            if on_progress and now - last_persist_at >= self.PROGRESS_PERSIST_SEC:
                try:
                    on_progress()
                except Exception:
                    traceback.print_exc()
                last_persist_at = time.time()
            active_tool = False
            finished_now = False
            turn_last = None      # 本轮自己的最后一条 assistant（盲区判定用）
            for m in reversed(msgs):
                if m.get('role') == 'assistant':
                    if baseline_ids and m.get('id') in baseline_ids:
                        continue  # message from before this (continued) turn
                    last_preview = self._preview_from(m)
                    last_message = m
                    turn_last = m
                    # OpenCode 明说这一轮挂了 —— 立刻带着原因失败，不要等 STALL。
                    # 放在 finished 判断之前：出错的消息有时也会带上终态 finish，
                    # 那种情况下当成"成功"会更糟（子任务标 completed、输出为空）。
                    if m.get('error'):
                        raise _TurnFailed(m['error'])
                    if m.get('finished'):
                        # 不再单点返回：进入稳定窗（tool-calls 裁决间隙 /
                        # autocontinue 与「回合真结束」此刻的快照不可区分）。
                        finished_now = True
                    # The model is mid tool-call (incl. delegating to a subagent
                    # via the `task` tool) — it's working, not stalled.
                    if m.get('running_tool') or m.get('finish') in ('tool-calls', 'tool_use'):
                        active_tool = True
                    break
            # 完成稳定窗：连续 FINISH_CONFIRM_POLLS 个轮询 finished 成立且
            # 本轮 assistant id 集合不变才放行；期间冒新消息（集合变化）计数
            # 重置、判定失效（最后一条不 finished）候选清空。
            if finished_now:
                cur_ids = frozenset(
                    x.get('id') for x in msgs
                    if x.get('role') == 'assistant' and x.get('id') is not None
                    and not (baseline_ids and x.get('id') in baseline_ids))
                if cand_ids == cur_ids:
                    cand_polls += 1
                else:
                    cand_ids, cand_polls = cur_ids, 1
                if cand_polls >= self.FINISH_CONFIRM_POLLS:
                    return self._finish_with_guarantee(
                        oc_session_id, sid, directory, msgs, baseline_ids,
                        last_preview, last_message, cand_ids=cand_ids,
                        lease_lost=lease_lost, batch_id=batch_id)
            else:
                cand_ids, cand_polls = None, 0
            # Watchdogs. 签名变化 = 有进展：重置两个计时器。工具在跑但父级输出
            # 冻结时，no-progress 看门狗保持安静（既有限制），同时给这个纹丝
            # 不动的工具调用单独计时（tool-stuck 看门狗）。
            sig = self._progress_signature(msgs)
            if first or sig != last_sig:
                first = False
                last_sig = sig
                last_progress_at = now
                tool_stall_start = now if active_tool else None
            elif active_tool:
                last_progress_at = now
                if (self.TOOL_STALL_TIMEOUT_SEC > 0
                        and tool_stall_start is not None
                        and now - tool_stall_start > self.TOOL_STALL_TIMEOUT_SEC):
                    if sid in self._gate_checking:   # _gate_checking 存的是内部 sid
                        tool_stall_start = now   # 委派级判官核对中：豁免
                    elif self._subagent_progressing(msgs, baseline_ids, directory,
                                                  child_sigs):
                        tool_stall_start = now   # 子代理还活着：顺延等待
                    else:
                        # 卡死的工具调用：先 abort（别让 OpenCode 继续空转烧
                        # token）再失败，带着可读原因与判定现场。
                        try:
                            opencode_client.abort_session(oc_session_id, directory=directory)
                        except Exception:
                            traceback.print_exc()
                        raise _SessionTimeout(
                            int(now - tool_stall_start), reason='tool stuck',
                            diagnosis=self._stall_diagnosis(
                                msgs, baseline_ids, last_sig,
                                directory, oc_session_id))
            elif now - last_progress_at > self.STALL_TIMEOUT_SEC:
                # 思考盲区分层（2026-11-04 生产误报）：OpenCode 不流式落盘
                # reasoning，单次思考 >STALL 的活回合在轮询视角与真死不可
                # 区分。盲区形态（step 进行中 / 无任何实质 part）改用更长的
                # THINKING 阈值；其余冻结（已有输出后卡死）维持 STALL 秒杀。
                frozen = now - last_progress_at
                blindspot = self._thinking_blindspot(turn_last)
                limit = (self.THINKING_STALL_TIMEOUT_SEC
                         if blindspot and self.THINKING_STALL_TIMEOUT_SEC > 0
                         else self.STALL_TIMEOUT_SEC)
                if frozen > limit:
                    # 子代理作保（对齐 tool-stuck 分支语义，范围扩到全消息）：
                    # 新消息插队后旧消息里仍在跑的 task 委派不在最后一条上，
                    # 父签名冻结走不到 active_tool 豁免——但子代理自己的消息
                    # 还在推进时这不是死回合，顺延等待。
                    if self._children_progressing(
                            self._any_running_task_children(msgs, baseline_ids),
                            directory, child_sigs):
                        last_progress_at = now
                    else:
                        # 判死必须真死：不 abort 的话原回合在 OpenCode 里继续跑、
                        # 继续输出甚至完成——与任务行 failed 状态分裂（生产实测
                        # 「报 181 秒无进展但会话还在继续输出」，2026-11-03）。对齐
                        # tool-stuck 路径的 abort（先停掉别空转烧 token）；若判定后
                        # 走自动重试，continue_prompt 发进被 abort 的会话继续，同
                        # tool-stuck 的既有语义。
                        try:
                            opencode_client.abort_session(oc_session_id,
                                                          directory=directory)
                        except Exception:
                            traceback.print_exc()
                        raise _SessionTimeout(
                            int(frozen),
                            reason='stalled (no progress)',
                            diagnosis=self._stall_diagnosis(
                                msgs, baseline_ids, last_sig, directory,
                                oc_session_id))
            time.sleep(self.POLL_INTERVAL_SEC)
        # 硬上限到点时若稳定窗已有 ≥1 次确认（回合实际已完结、只差确认轮询），
        # 按完成收口而不是超时——稳定窗不应把最后一刻完成的回合误判成超时失败。
        if cand_polls >= 1:
            return self._finish_with_guarantee(
                oc_session_id, sid, directory, msgs, baseline_ids,
                last_preview, last_message, cand_ids=cand_ids,
                lease_lost=lease_lost, batch_id=batch_id)
        raise _SessionTimeout(self.SESSION_TIMEOUT_SEC)

    def _finish_with_guarantee(self, oc_session_id: str, sid: str,
                               directory: str, msgs: list,
                               baseline_ids: set | None,
                               last_preview, last_message, *,
                               cand_ids: 'frozenset | None',
                               lease_lost, batch_id: str | None):
        """完成放行的三重保证（不变式：completed ⟹ OC 回合已终结）。

        1. 租约检查：失守则不 abort（接管者可能正在复用该 OpenCode 会话，
           与 _LeaseLost 处理器/F4 语义一致），交回上层静默放手；
        2. 止血 abort：best-effort abort 主会话与在跑的 task 子代理。回合真
           结束时为幂等 no-op（routes/ai_chat.py abort 端点的幂等契约），
           判定过早时杀死 zombie——这是不变式的硬保证，异常只记日志；
        3. 终拉对账：abort 后再拉一次消息——id 集合比候选判定时刻多 =
           判定过早实锤（新消息已被 abort 杀掉），记 child.premature_finish
           事件把静默分叉变成可取证证据；返回值刷新为终拉里更完整的最后
           一条 assistant。终拉失败不阻塞完成（回退候选时刻的快照）。
        """
        if lease_lost is not None and lease_lost.is_set():
            raise _LeaseLost()
        if self.ABORT_ON_FINISH:
            for target in [oc_session_id] + self._running_task_children(
                    msgs, baseline_ids):
                try:
                    opencode_client.abort_session(target, directory=directory)
                except Exception:  # noqa: BLE001 —— 止血是 best-effort
                    logger.warning('[finish-guarantee] abort %s failed sid=%s',
                                   target, sid, exc_info=True)
        try:
            final = opencode_client.list_messages(oc_session_id,
                                                  directory=directory) or []
        except Exception:  # noqa: BLE001 —— 对账失败不阻塞完成
            logger.warning('[finish-guarantee] final pull failed sid=%s '
                           '(fallback to confirmed snapshot)', sid)
            return last_preview, last_message
        final_ids = frozenset(
            x.get('id') for x in final
            if x.get('role') == 'assistant' and x.get('id') is not None
            and not (baseline_ids and x.get('id') in baseline_ids))
        grew = sorted(final_ids - (cand_ids or frozenset()))
        if grew:
            logger.warning('[finish-guarantee] premature completion sid=%s: '
                           'messages appeared after confirmation %s (aborted)',
                           sid, grew)
            if batch_id:
                try:
                    from ai.batch import batch_events as batch_events
                    batch_events.append_event(
                        batch_id, 'child.premature_finish',
                        aggregate_type='session', aggregate_id=sid,
                        payload={'session_id': sid, 'newMessages': grew,
                                 'confirmedIds': sorted(cand_ids or ())})
                except Exception:  # noqa: BLE001 —— 取证 best-effort
                    logger.warning('[finish-guarantee] premature event failed '
                                   'sid=%s', sid, exc_info=True)
        for m in reversed(final):
            if m.get('role') != 'assistant':
                continue
            if baseline_ids and m.get('id') in baseline_ids:
                continue
            return self._preview_from(m), m
        return last_preview, last_message

    @staticmethod
    def _has_substantive_part(msg: dict) -> bool:
        """消息里是否有 REST 可见的实质内容（非空 text/reasoning 或工具
        part）——思考盲区判定的底座：OpenCode 思考期间这些全都不存在。"""
        content = msg.get('content') or []
        return any(
            ((p.get('type') in ('text', 'reasoning') and p.get('text'))
             or p.get('type') == 'tool_use')
            for p in content if isinstance(p, dict))

    @staticmethod
    def _thinking_blindspot(turn_last: dict | None) -> bool:
        """本轮是否处于「思考盲区」：OpenCode 只在 step 结束时一次性落盘
        reasoning 文本（实测 68s 的思考期间 part/message/session 的 REST
        视角全部零增量），思考进行中的回合与 provider 真死在轮询视角不可
        区分——只能识别形态、拉长阈值，不能免除判死。

        形态二选一：
        1. step 进行中（step-start 已落盘、step-finish 未落）——新版 OC
           信号，消息里即便已有旧 step 的 text/tool part 也算；
        2. 最后一条消息无任何实质 part（无非空 text/reasoning、无
           tool_use）且未终结未报错——涵盖回合刚派发还没首条消息、以及
           旧版 OC（无 step 标记）纯思考的兜底。"""
        if turn_last is None:
            return True     # 首条 assistant 还没落：模型在思考/网关在排队
        if turn_last.get('error') or turn_last.get('finished'):
            return False
        if turn_last.get('step_active'):
            return True
        # BatchWorker 是组合类名：runner 导入期无此名（见本模块尾部占位 +
        # worker.py 尾部回填——拆分架构必需的交叉引用），调用期解析为真实
        # 组合类；main 单体内为同类引用，此处是逐字纪律的认可例外。
        return not BatchWorker._has_substantive_part(turn_last)  # noqa: F821

    @staticmethod
    def _progress_signature(msgs: list) -> tuple:
        """A cheap proxy for forward progress: (#assistant messages, total text
        length, tool activity, open steps). Changes whenever the model emits a
        new message, more text, OR a tool advances (new tool call / status
        change / growing output) — so a delegating subagent (a long-running
        `task` tool) counts as progress instead of looking 'stalled'.
        step 标记同样算进展：step-start 在 step 开始瞬间落盘、step-finish 在
        结束时落盘（reasoning 文本只在 step 结束时一次性可见），多回合
        「思考→工具→再思考」的每个 step 边界都会刷新冻结计时。"""
        count = 0
        total_text = 0
        tool_sig: list = []
        open_steps = 0
        for m in msgs:
            if m.get('role') == 'assistant':
                count += 1
                if m.get('step_active'):
                    open_steps += 1
                for p in (m.get('content') or []):
                    if p.get('type') in ('text', 'reasoning'):
                        total_text += len(p.get('text') or '')
                    elif p.get('type') == 'tool_use':
                        tool_sig.append((p.get('name'), p.get('status'),
                                         p.get('output_len') or 0))
        return (count, total_text, tuple(tool_sig), open_steps)

    def _stall_diagnosis(self, msgs: list, baseline_ids: set | None,
                         last_sig, directory: str,
                         oc_session_id: str) -> dict:
        """STALL / 工具卡死触发瞬间的判定现场快照。

        回答三问（事后区分「provider 真死 / 平台轮询看不见 / ask 挂起没被
        自动拒」的一手证据）：当时最后一条 assistant 消息长什么样（finish/
        running_tool/工具 part 状态）、有没有 pending question/permission、
        最后一次进度签名是什么。纯读取 + 防御式 REST 计数——诊断绝不能再
        炸失败路径本身，任何异常都降级为缺键。"""
        d: dict = {}
        try:
            d['msgCount'] = len(msgs)
            for m in reversed(msgs):
                if m.get('role') != 'assistant':
                    continue
                if baseline_ids and m.get('id') in baseline_ids:
                    continue  # continue 模式：上一回合的旧消息不算本轮现场
                d['lastMsgId'] = m.get('id')
                d['finish'] = m.get('finish')
                d['runningTool'] = bool(m.get('running_tool'))
                d['stepActive'] = bool(m.get('step_active'))
                err = m.get('error')
                if err:
                    d['msgError'] = (err.get('name')
                                     if isinstance(err, dict) else str(err))
                parts = [p for p in (m.get('content') or [])
                         if isinstance(p, dict) and p.get('type') == 'tool_use']
                d['toolParts'] = [{'name': p.get('name'), 'status': p.get('status')}
                                  for p in parts[-5:]]   # 最近 5 个，防长尾
                # 思考盲区标志（2026-11-04）：事后区分「盲区误杀（真在思考）」
                # 与「有输出后真死」——前者 emptyParts/stepActive 为真。
                d['emptyParts'] = not self._has_substantive_part(m)
                break
            if last_sig is not None:
                d['sig'] = str(last_sig)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        # pending ask 计数走独立端点（消息列表里看不到），连同正在跑的
        # 子代理会话一起扫——子代理的提问挂起的是父回合的 task 工具。
        accepted = {oc_session_id}
        try:
            accepted |= set(self._running_task_children(msgs, baseline_ids))
        except Exception:  # noqa: BLE001
            pass
        try:
            qs = opencode_client.list_questions(directory=directory) or []
            d['pendingQuestions'] = sum(
                1 for q in qs
                if isinstance(q, dict) and q.get('sessionID') in accepted)
        except Exception:  # noqa: BLE001
            pass
        try:
            ps = opencode_client.list_permissions(directory=directory) or []
            d['pendingPermissions'] = sum(
                1 for p in ps
                if isinstance(p, dict) and p.get('sessionID') in accepted)
        except Exception:  # noqa: BLE001
            pass
        return d

    @staticmethod
    def _stop_requested(session_id: str) -> str | None:
        """一次查询同时取两个协作式停止标志：'cancel'（中断，落 cancelled/
        failed 计数）或 'pause'（暂停，落 paused/不占计数），都没有则 None。
        取消优先：两个标志理论上互斥（pause 与 cancel 是互斥的用户动作），
        万一同时置位按取消处理。"""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cancel_requested, pause_requested "
                    "FROM ai_chat_sessions WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        if row[0]:
            return 'cancel'
        if row[1]:
            return 'pause'
        return None

    def _reject_pending_questions(self, oc_session_id: str, directory: str = '',
                                  extra_sids: set | None = None):
        """无人值守兜底：模型调用 question 工具向用户提问会把回合无限期挂起
        （批任务没有用户能回答）。发现本会话的 pending 问题就自动拒绝 ——
        OpenCode 把该工具调用标记为 rejected 并让模型继续（与交互聊天里用户
        点「拒绝」是同一条机制）。全部防御式包装：OpenCode 不可达或返回畸形
        数据都直接跳过，绝不因这个辅助检查打断主轮询。

        `extra_sids`：本回合在跑的子代理会话 id（父消息流里 task 工具给出的
        `child_sid`）。子代理的提问同样无人能答，而它阻塞的是**父回合**——只
        按父会话 id 过滤会让它漏网，父回合要等 900s 工具卡死看门狗才失败。"""
        accepted = {oc_session_id} | set(extra_sids or ())
        try:
            pending = opencode_client.list_questions(directory=directory)
            items = [q for q in (pending or []) if isinstance(q, dict)]
        except Exception:
            return
        for q in items:
            if q.get('sessionID') not in accepted or not q.get('id'):
                continue
            try:
                # OC2 双栈：v2 会话的 form cancel 端点是会话作用域——按归属
                # 会话的绑定栈分流（V1 客户端不收 oc_session_id 形参）
                _own_sid = q.get('sessionID') or ''
                _client_q = opencode_client._client(oc_session_id=_own_sid)
                try:
                    from ai.runtime import registry as _reg
                    _is_v2 = _reg.runtime_kind_by_oc_id(_own_sid) == 'v2'
                except Exception:
                    _is_v2 = False
                if _is_v2:
                    _client_q.reject_question(q['id'], directory=directory,
                                              oc_session_id=_own_sid)
                else:
                    _client_q.reject_question(q['id'], directory=directory)
                logger.info('batch auto-rejected question oc=%s qid=%s',
                            oc_session_id, q['id'])
            except Exception:
                traceback.print_exc()

    def _reject_pending_permissions(self, oc_session_id: str, directory: str = '',
                                    extra_sids: set | None = None):
        """无人值守兜底（2026-09-30）：批子会话工作区已写 external_directory
        allow，但其他权限类型（如 doom_loop）仍可能触发 ask——权限询问同样
        无人能答、无限期挂起。发现本会话的挂起权限请求就自动 reject（模型
        收到拒绝后自行继续）。防御式包装同 _reject_pending_questions。

        `extra_sids` 同 _reject_pending_questions：子代理会话的权限询问一并
        拒绝（它阻塞的是父回合的 task 工具，父级看门狗的「子代理作保」还会
        因此顺延不杀——不扫就会挂到很久）。"""
        accepted = {oc_session_id} | set(extra_sids or ())
        try:
            pending = opencode_client.list_permissions(directory=directory)
            items = [q for q in (pending or []) if isinstance(q, dict)]
        except Exception:
            return
        for q in items:
            if q.get('sessionID') not in accepted or not q.get('id'):
                continue
            try:
                # OC2 双栈：v2 的权限 reply 端点会话作用域——按归属会话绑定
                # 栈分流（V1 客户端不收 oc_session_id 形参）
                _own_sid = q.get('sessionID') or ''
                _client_p = opencode_client._client(oc_session_id=_own_sid)
                try:
                    from ai.runtime import registry as _reg
                    _is_v2 = _reg.runtime_kind_by_oc_id(_own_sid) == 'v2'
                except Exception:
                    _is_v2 = False
                if _is_v2:
                    _client_p.reply_permission(q['id'], 'reject',
                                               directory=directory,
                                               oc_session_id=_own_sid)
                else:
                    _client_p.reply_permission(q['id'], 'reject',
                                               directory=directory)
                logger.info('batch auto-rejected permission oc=%s pid=%s (%s)',
                            oc_session_id, q['id'], q.get('permission'))
            except Exception:
                traceback.print_exc()

    @staticmethod
    def _running_task_children(msgs: list, baseline_ids: set | None) -> list:
        """最新（非基线）assistant 消息里仍在运行、且带子代理会话 id 的 task
        工具列表 —— 工具卡死看门狗的"子代理作保"候选。"""
        for m in reversed(msgs):
            if m.get('role') != 'assistant':
                continue
            if baseline_ids and m.get('id') in baseline_ids:
                continue
            return [p.get('child_sid') for p in (m.get('content') or [])
                    if p.get('type') == 'tool_use' and p.get('name') == 'task'
                    and p.get('status') in (None, '', 'pending', 'running')
                    and p.get('child_sid')]
        return []

    @staticmethod
    def _any_running_task_children(msgs: list, baseline_ids: set | None) -> list:
        """全轮非基线消息范围的在跑 task 子代理（stall 作保用）。

        _running_task_children 只看最后一条 assistant——新消息插队（模型在
        委派未返回时又产出一条消息）后，旧消息里仍在跑的委派会被漏掉，stall
        分支拿不到作保候选而误杀活着的子代理。"""
        out: list = []
        for m in msgs:
            if m.get('role') != 'assistant':
                continue
            if baseline_ids and m.get('id') in baseline_ids:
                continue
            out += [p.get('child_sid') for p in (m.get('content') or [])
                    if p.get('type') == 'tool_use' and p.get('name') == 'task'
                    and p.get('status') in (None, '', 'pending', 'running')
                    and p.get('child_sid')]
        return out

    def _children_progressing(self, children: list, directory: str,
                              state: dict) -> bool:
        """给定子代理会话 id 列表，逐个比对其**自己的**消息签名判断存活。

        父会话的消息签名在子代理工作期间是不变的（子代理的输出要等工具返回
        才进父级消息），所以用子代理自己的消息列表签名判断：签名变了 = 活着
        → 看门狗顺延。查不到子代理消息时按"无法判断"处理（宁可多等一个
        窗口），绝不因辅助检查的抖动误杀长跑子代理；但**连续**查不到
        （CHILD_PROBE_ERROR_BUDGET 次以上）不再作保——持续断连时顺延会变
        成永活，看门狗失效。
        `state` 跨轮询持有每个子代理上一次的签名（调用方传同一个 dict）。
        """
        if not children:
            return False
        progressing = False
        for csid in children:
            try:
                raw = opencode_client.get_messages(csid, directory=directory) or []
                sig = self._raw_progress_signature(raw)
            except Exception:
                errs = state.get(csid, {}).get('errors', 0) \
                    if isinstance(state.get(csid), dict) else 0
                errs += 1
                state[csid] = {'errors': errs}
                if errs <= self.CHILD_PROBE_ERROR_BUDGET:
                    progressing = True   # 瞬时查不到 ≠ 死了
                continue
            if isinstance(state.get(csid), dict):
                # 从错误态恢复：这次成功读到了，重新观测
                state[csid] = None
            if state.get(csid) is None:
                state[csid] = sig
                progressing = True   # 首次观测：先算活着，下个窗口做对比
            elif sig != state[csid]:
                state[csid] = sig
                progressing = True
        return progressing

    def _subagent_progressing(self, msgs: list, baseline_ids: set | None,
                              directory: str, state: dict) -> bool:
        """冻结窗口内，在跑的 task 工具背后的子代理是否仍有产出（工具卡死
        看门狗用：只看最后一条消息的在跑委派）。没有任何在跑的 task 工具 →
        False（让卡死的普通工具调用被杀）。"""
        return self._children_progressing(
            self._running_task_children(msgs, baseline_ids),
            directory, state)

    @staticmethod
    def _raw_progress_signature(msgs: list) -> tuple:
        """_progress_signature 的 raw 形态：直接吃 OpenCode REST 消息列表
        ([{'info','parts'}])，供子代理存活检测用（那边不做 facade 映射）。"""
        count = 0
        total_text = 0
        tool_sig: list = []
        for m in msgs:
            info = m.get('info') or {}
            if info.get('role') != 'assistant':
                continue
            count += 1
            for p in (m.get('parts') or []):
                t = p.get('type')
                if t in ('text', 'reasoning'):
                    total_text += len(p.get('text') or '')
                elif t == 'tool':
                    st = p.get('state') or {}
                    out = st.get('output')
                    tool_sig.append((p.get('tool'), st.get('status'),
                                     len(out) if isinstance(out, str) else 0))
        return (count, total_text, tuple(tool_sig))

__all__ = [
    '_BATCH_DIRECTIVE',
    '_NO_WINDOW',
    'logger',
    'AI_WORKSPACE_ROOT',
    'get_db',
    'create_session_workspace',
    '_rm_force',
    'batch_workspace_root',
    'legacy_batch_workspace_root',
    'resolve_batch_data_path',
    'git_changes',
    'record_session_files',
    'render_history_block',
]

# BatchWorker 占位（_thinking_blindspot 调用期解析）：runner 由 worker.py 顶部
# 导入（WorkerRunnerMixin），导入期反向 from .worker import 会撞半初始化。
# 此占位让静态分析可见该名字；worker.py 定义组合类后在尾部回填覆盖（见
# worker.py「包内交叉引用」段）——调用期必为真实类。刻意不进 __all__：
# 聚合命名空间符号面不变（symbol_parity 基线约束）。
BatchWorker = None
