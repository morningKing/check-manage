import logging
import traceback

from db import get_db
from common.notifier import create_notification

from .discovery import _require_fencing
from .lifecycle import _notify_callback

logger = logging.getLogger(__name__)

# 自动重排队原因白名单（_close_attempt_for_requeue 落 attempt.error_code）。
# perf 分析的端到端 run 口径据此区分「自动恢复续跑」与「人工接管续跑」
# （后者 parent_attempt_id 恒 NULL）。_consume_parent_attempt 兜底查库同用。
AUTO_REQUEUE_CODES = ('AUTO_RETRY', 'GATE_RETRY', 'RECONCILE_REQUEUE')


def _fmt_elapsed(secs: float) -> str:
    """把耗时秒数格式化为「X 分 Y 秒」/「Y 秒」（与交互式长任务通知一致）。"""
    s = max(0, int(secs))
    return f'{s // 60} 分 {s % 60} 秒' if s >= 60 else f'{s} 秒'


class WorkerTransitionsMixin:
    def _close_attempt_for_requeue(self, session_id: str, reason: str,
                                   error_message: str | None = None):
        """重排（gate retry / auto retry / 对账重排）前的 attempt 收口（F9）：
        最新 running attempt 落 'recovering'（不是 completed/failed——该轮
        没有真正收敛），并把 id 记入 _parent_attempt 供下一次 create_attempt
        串 parent_attempt_id 链。error_message 落具体异常文本（≤2000 字由
        finish_latest_running 截断）——error_code 只是类别，取证要靠它。"""
        from ai.batch import execution_audit as execution_audit
        closed = execution_audit.finish_latest_running(
            session_id, 'recovering', error_code=reason,
            error_message=error_message)
        if closed:
            self._parent_attempt[session_id] = closed

    def _consume_parent_attempt(self, session_id: str):
        """下一次 create_attempt 的 parent_attempt_id（端到端 run 口径判据，
        2026-11-02）：优先消费内存字典（_close_attempt_for_requeue 记录）；
        字典未命中（worker 重启丢失）回落查库——仅当**最新一条** attempt 为
        status='recovering' 且 error_code 属自动重排队白名单才可作链父。
        必须限「最新一条」：自动重试已收敛（末条 completed）后的人工
        continue 不得串上更早的 recovering 旧链。"""
        parent = self._parent_attempt.pop(session_id, None)
        if parent:
            return parent
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM ai_execution_attempts "
                        "WHERE session_id = %s AND attempt_no = "
                        "  (SELECT MAX(attempt_no) FROM ai_execution_attempts "
                        "    WHERE session_id = %s) "
                        "AND status = 'recovering' "
                        "AND error_code = ANY(%s)",
                        (session_id, session_id,
                         list(AUTO_REQUEUE_CODES)))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:      # 审计链兜底失败不阻断派发（_safe 惯例）
            logger.warning('parent attempt fallback lookup failed sid=%s: %s',
                           session_id, e)
            return None

    def _notify_child_done(self, session_row: dict, ok: bool,
                           elapsed: float | None = None, error: str | None = None):
        """批任务每个子会话到达终态时，给任务所有者发一条站内通知（F9）。

        批任务是用户发起后通常会离开的异步作业，所以这里**默认开启**、不依赖
        浏览器开关/通知权限，标签页关闭也不丢——下次打开页面铃铛即显示未读。
        成功与失败都通知（失败恰恰是离开后最需要知道的）；用户主动取消不通知。
        通知 source 指向 ai-chat 会话，铃铛点击即 /ai-chat?session=<sid>。
        best-effort：通知失败不影响子任务状态落库。

        定时扫描（轨迹分析）子会话不在此通知：一次调度会按记录数产出几十上百个
        子会话且按计划反复运行，逐个发铃铛会把通知中心刷爆；其结果通过数据表
        状态回写（ai_scan_engine.on_child_finished）呈现，与交互式路径跳过
        「轨迹分析:」会话的处理保持一致。"""
        try:
            sid = session_row.get('id')
            user_id = session_row.get('user_id')
            # 仅批任务子会话通知；open-api 独立子会话（batch_id 为空）是程序化调用，
            # 不产生界面铃铛通知。
            if not sid or not user_id or not session_row.get('batch_id'):
                return
            # 定时扫描子会话：高并发 + 周期性自动运行，结果另有数据表回写界面，不发铃铛。
            if session_row.get('scan_task_id'):
                return
            title = session_row.get('title') or '子任务'
            dur = _fmt_elapsed(elapsed) if elapsed is not None else ''
            if ok:
                create_notification(
                    user_id,
                    'aiBatchChildDone',
                    f'批任务子任务已完成：{title}',
                    f'耗时 {dur}，点击查看对话。' if dur else '点击查看对话。',
                    source_collection='ai-chat',
                    source_record_id=sid,
                )
            else:
                reason = f'（{error[:80]}）' if error else ''
                create_notification(
                    user_id,
                    'aiBatchChildFailed',
                    f'批任务子任务失败：{title}',
                    f'子任务未成功完成{reason}，点击查看。',
                    source_collection='ai-chat',
                    source_record_id=sid,
                )
        except Exception as e:
            logger.debug('batch child turn-done notification failed sid=%s: %s',
                         session_row.get('id'), e)

    def _notify_scan(self, session_row, final_msg, ok: bool):
        if not session_row.get('scan_task_id'):
            return
        try:
            from ai.scan.ai_scan_engine import on_child_finished
            on_child_finished(session_row, final_msg, ok=ok)
        except Exception:
            traceback.print_exc()

    # --- 终态落库（P0 CAS：唯一入口 repo.transition_child，spec §6.1）---

    @staticmethod
    def _resolve_generation(session_id: str, generation: int | None) -> int:
        """写回用的 execution_generation：调用方（_run_one）传 claim 时捕获的
        值；直接调用（旧测试/外部）没传时从 DB 现读——读到什么就用什么，
        由 CAS 的 WHERE 条件保证不匹配时 0 行。"""
        if generation is not None:
            return generation
        from utils import batch_repo
        st = batch_repo.read_child_control_state(session_id)
        return (st or {}).get('execution_generation') or 0

    def _on_transition_miss(self, session_id: str, batch_id: str | None,
                            generation: int | None, attempted: str,
                            *, redirect: bool):
        """CAS 未命中（rowcount=0）的统一处理：子会话已被并发收口或已换代。
        redirect=True（来自 done/failed 写回）时按当前控制标志重定向到
        cancel/pause 落库；redirect=False（cancel/pause 写回自身未命中）只记
        日志即止，绝不递归。"""
        from utils import batch_repo
        st = batch_repo.read_child_control_state(session_id)
        if st is None:
            return  # 行已消失（批被删，FK CASCADE）
        if st['status'] != 'running':
            logger.info('stale terminal write ignored sid=%s attempted=%s '
                        'current=%s (already settled)', session_id, attempted,
                        st['status'])
            return
        if redirect and st.get('cancel_requested'):
            self._mark_cancelled(session_id, batch_id,
                                 generation=st['execution_generation'],
                                 fencing_token=st['fencing_token'])
            return
        if redirect and st.get('pause_requested'):
            self._mark_paused(session_id, batch_id,
                              generation=st['execution_generation'],
                              fencing_token=st['fencing_token'])
            return
        logger.info('stale terminal write ignored sid=%s attempted=%s gen=%s',
                    session_id, attempted, generation)

    def _mark_done(self, session_id: str, batch_id: str,
                   last_preview: str | None, generation: int | None = None,
                   gate_status: str | None = None,
                   gate_error: str | None = None,
                   fencing_token: int | None = None):
        """完成落库。CAS（running+generation+无控制标志）命中时同一事务完成
        计数与批次状态重算；未命中按控制标志重定向或忽略，**不**重复计数。"""
        _require_fencing(fencing_token)
        from utils import batch_repo
        reparked = batch_repo.try_repark_transition(
            session_id, generation=self._resolve_generation(session_id, generation),
            fencing_token=fencing_token)
        if reparked is not None:
            # 接管轮收口（spec D3）：回 paused 交还外部系统，不进终态不计数。
            from ai.batch import execution_audit as execution_audit
            execution_audit.finish_latest_running(session_id, 'stopped',
                                                  error_code='TAKEOVER_REPARK')
            from ai.skills import skill_fit as _sfit
            _sfit.compute_for_session(session_id)
            return
        res = batch_repo.transition_child(
            session_id, 'completed',
            generation=self._resolve_generation(session_id, generation),
            last_message_preview=last_preview, gate_status=gate_status,
            gate_error=gate_error, count='done', turn_status='completed',
            fencing_token=fencing_token)
        if res is None:
            self._on_transition_miss(session_id, batch_id, generation,
                                     'completed', redirect=True)
            return
        from ai.batch import execution_audit as execution_audit
        execution_audit.finish_latest_running(session_id, 'completed')
        # SkillOpt 拟合收敛钩子：attempt 收口即算拟合（best-effort，失败仅记日志）
        from ai.skills import skill_fit as _sfit
        _sfit.compute_for_session(session_id)
        if res.get('batch_status') in ('completed', 'partial', 'failed'):
            _notify_callback(batch_id, res['batch_status'],
                             res.get('callback_url'), res.get('callback_secret'),
                             res.get('done'), res.get('failed'), res.get('total'))

    def _mark_failed(self, session_id: str, batch_id: str, error: str,
                     generation: int | None = None,
                     gate_status: str | None = None,
                     gate_error: str | None = None,
                     fencing_token: int | None = None):
        _require_fencing(fencing_token)
        from utils import batch_repo
        from ai.batch import execution_audit as execution_audit
        reparked = batch_repo.try_repark_transition(
            session_id, generation=self._resolve_generation(session_id, generation),
            fencing_token=fencing_token)
        if reparked is not None:
            # 接管轮收口（spec D3）：回 paused 交还外部系统，不进终态不计数。
            # 必须在 failed 审计之前判定——接管轮按 stopped 收口，绝不记 failed。
            execution_audit.finish_latest_running(session_id, 'stopped',
                                                  error_code='TAKEOVER_REPARK')
            from ai.skills import skill_fit as _sfit
            _sfit.compute_for_session(session_id)
            return
        # 本 attempt 确实跑完并失败：无论会话行 CAS 是否命中都收口审计，
        # 不留悬挂 running attempt（F9 的终态侧）。
        execution_audit.finish_latest_running(
            session_id, 'failed', error_code='CHILD_FAILED', error_message=error)
        # SkillOpt 拟合收敛钩子：finish 已执行即算拟合（CAS 未命中 early-return
        # 也在其后，不能放到方法尾部，否则漏掉 miss 路径）。
        from ai.skills import skill_fit as _sfit
        _sfit.compute_for_session(session_id)
        res = batch_repo.transition_child(
            session_id, 'failed',
            generation=self._resolve_generation(session_id, generation),
            error_message=error, gate_status=gate_status, gate_error=gate_error,
            count='failed', turn_status='failed',
            fencing_token=fencing_token)
        if res is None:
            self._on_transition_miss(session_id, batch_id, generation,
                                     'failed', redirect=True)
            return
        if res.get('batch_status') in ('completed', 'partial', 'failed'):
            _notify_callback(batch_id, res['batch_status'],
                             res.get('callback_url'), res.get('callback_secret'),
                             res.get('done'), res.get('failed'), res.get('total'))

    def _mark_cancelled(self, session_id: str, batch_id: str | None,
                        generation: int | None = None,
                        fencing_token: int | None = None):
        """Same shape as _mark_failed (cancelled counts toward the batch's
        `failed` aggregate — see batch_repo.cancel_batch / _mark_paused for the
        contrast), but writes the literal 'cancelled' status so callers can tell
        a deliberate cancel apart from a genuine error. CAS 带
        cancel_requested=true 期望（spec §6.1：取消按取消条件收口）。"""
        _require_fencing(fencing_token)
        from utils import batch_repo
        from ai.batch import execution_audit as execution_audit
        execution_audit.finish_latest_running(
            session_id, 'stopped', error_code='CANCELLED')
        # SkillOpt 拟合收敛钩子：finish 已执行即算拟合（同 _mark_failed，钩子
        # 必须在 CAS 未命中 early-return 之前）。
        from ai.skills import skill_fit as _sfit
        _sfit.compute_for_session(session_id)
        res = batch_repo.transition_child(
            session_id, 'cancelled',
            generation=self._resolve_generation(session_id, generation),
            error_message='已被调用方取消', expect_cancel=True,
            # cancel 优先（spec §4.2）：并存 pause 标志不再挡取消收口；
            # expect_repark=None 同理——人工接管轮（continue_repark=true）
            # 运行期间取消必须立即生效，不得等 repark 落 paused 再取消。
            expect_pause=None, expect_repark=None,
            fencing_token=fencing_token,
            count='failed', turn_status='cancelled')
        if res is None:
            self._on_transition_miss(session_id, batch_id, generation,
                                     'cancelled', redirect=False)

    def _mark_paused(self, session_id: str, batch_id: str | None,
                     generation: int | None = None,
                     fencing_token: int | None = None):
        """协作式暂停的落库：status='paused'。与 _mark_cancelled 的区别是**不占
        failed 计数**（暂停不是失败，批次还能整体 resume），error_message 留空。
        CAS 带 pause_requested=true 且 cancel 优先（spec §4.2：cancel > pause）。"""
        _require_fencing(fencing_token)
        from utils import batch_repo
        from ai.batch import execution_audit as execution_audit
        execution_audit.finish_latest_running(session_id, 'stopped',
                                              error_code='PAUSED')
        res = batch_repo.transition_child(
            session_id, 'paused',
            generation=self._resolve_generation(session_id, generation),
            expect_pause=True, turn_status='paused',
            fencing_token=fencing_token)
        if res is None:
            self._on_transition_miss(session_id, batch_id, generation,
                                     'paused', redirect=False)

    def _mark_needs_review(self, session_id: str, batch_id: str | None,
                           reason: str, generation: int | None = None,
                           fencing_token: int | None = None):
        """needs_review 终态（P1 spec §4.2）：无法自动安全继续，需人工判断，
        不得自动重放。计入 failed 聚合（与 cancelled 同桶），子任务行保留
        字面 'needs_review' 状态供管理面展示。"""
        _require_fencing(fencing_token)
        from utils import batch_repo
        from ai.batch import execution_audit as execution_audit
        execution_audit.finish_latest_running(
            session_id, 'stopped', error_code='NEEDS_REVIEW',
            error_message=reason)
        # SkillOpt 拟合收敛钩子：finish 已执行即算拟合（同 _mark_failed，钩子
        # 必须在 CAS 未命中 early-return 之前）。
        from ai.skills import skill_fit as _sfit
        _sfit.compute_for_session(session_id)
        res = batch_repo.transition_child(
            session_id, 'needs_review',
            generation=self._resolve_generation(session_id, generation),
            error_message=reason, count='failed',
            turn_status='cancelled', fencing_token=fencing_token)
        if res is None:
            self._on_transition_miss(session_id, batch_id, generation,
                                     'needs_review', redirect=False)
            return
        if res.get('batch_status') in ('completed', 'partial', 'failed'):
            _notify_callback(batch_id, res['batch_status'],
                             res.get('callback_url'), res.get('callback_secret'),
                             res.get('done'), res.get('failed'), res.get('total'))


__all__ = [
    'AUTO_REQUEUE_CODES',
    '_fmt_elapsed',
    'logger',
    'get_db',
    'create_notification',
]
