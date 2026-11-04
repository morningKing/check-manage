from psycopg2.extras import RealDictCursor

from db import get_db

from .lifecycle import _recompute_batch_status_for, RESUME_CONTINUE_PROMPT
from .queries import get_batch_detail
from .status import recompute_batch_status_tx


# transition_child 的「未传 preview」哨兵：传了 None 表示显式清空，
# 不传表示保持原值（P0 spec §6.1 的 COALESCE 语义由调用方区分）。
_PREVIEW_UNSET = object()


def transition_child(session_id: str, new_status: str, *, generation: int,
                     error_message: str | None = None,
                     last_message_preview=_PREVIEW_UNSET,
                     gate_status: str | None = None,
                     gate_error: str | None = None,
                     expect_cancel: bool | None = False,
                     expect_pause: bool | None = False,
                     expect_repark: bool | None = False,
                     count: str | None = None,
                     turn_status: str | None = None,
                     fencing_token: int) -> dict | None:
    """P0 CAS 终态转移（ai-harness-p0 spec §6.1）——终态写入的唯一入口。

    WHERE 带 status='running' + execution_generation + cancel/pause/repark
    标志期望；rowcount=0 即写回过期（stale worker / 已被并发收口），返回
    None，调用方不得改状态、计数或消息。rowcount=1 时在同一事务内完成三件
    事再提交：批次计数 +1（count='done'/'failed'）→ 批次状态重算 → active
    turn 收口，消灭「计数已加、状态未写」的中间态。

    `generation` 是 claim 时捕获的 execution_generation；不匹配即 0 行。
    expect_cancel/expect_pause/expect_repark 三态：False=要求未置位
    （done/failed/paused）、True=要求已置位（cancelled / 接管轮 repark）、
    None=不关心（cancel 优先语义下，取消收口不应再被并存的 pause 标志
    挡住；expect_repark=None 供不想被接管标志影响的常规路径显式表态）。
    SET 恒清 continue_repark=false：接管收口（try_repark_transition）在此
    消费掉标志，其余路径本就不该带标志——防御性默认清除，对既有路径无害。
    `fencing_token` **必填**（M6，10 号 §3.3）：worker 路径传 claim 时捕获
    的 token（旧 token 写 0 行）；对账/重定向等"无 claim 权威"路径传决策
    查询里刚读出的当前值（自洽）。不再提供"未传则现读自比"的恒真兜底。
    返回 {'id','batch_id','batch_status','done','failed','total',
    'callback_url','callback_secret'}（batch 字段在 batch_id 为空时缺省）。
    """
    preview = None if last_message_preview is _PREVIEW_UNSET \
        else last_message_preview
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ai_chat_sessions
                   SET status = %s,
                       continue_repark = false,
                       active_turn_id = NULL,
                       error_message = %s,
                       last_message_preview = COALESCE(%s, last_message_preview),
                       gate_status = COALESCE(%s, gate_status),
                       gate_error = %s,
                       gate_checked_at = CASE WHEN %s IS NULL
                                              THEN gate_checked_at ELSE now() END
                 WHERE id = %s
                   AND status = 'running'
                   AND execution_generation = %s
                   AND (%s IS NULL OR cancel_requested = %s)
                   AND (%s IS NULL OR pause_requested = %s)
                   AND (%s IS NULL OR continue_repark = %s)
                   AND fencing_token = %s
                RETURNING id, batch_id
                """,
                (new_status, error_message, preview, gate_status, gate_error,
                 gate_status, session_id, generation,
                 expect_cancel, bool(expect_cancel),
                 expect_pause, bool(expect_pause),
                 expect_repark, bool(expect_repark),
                 int(fencing_token)),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return None
            sid, batch_id = row[0], row[1]
            if batch_id and count == 'done':
                cur.execute("UPDATE ai_chat_batches SET done = done + 1 "
                            "WHERE id = %s", (batch_id,))
            elif batch_id and count == 'failed':
                cur.execute("UPDATE ai_chat_batches SET failed = failed + 1 "
                            "WHERE id = %s", (batch_id,))
            batch = recompute_batch_status_tx(cur, batch_id, conn=conn,
                                              session_id=sid) \
                if batch_id else {}
            # P1：child.status 事件与状态写入同事务（spec §7.1）
            if batch_id:
                from ai.batch import batch_events as _be
                _be.append_event(batch_id, 'child.status',
                                 aggregate_type='child', aggregate_id=sid,
                                 execution_generation=generation,
                                 payload={'status': new_status,
                                          'gateStatus': gate_status},
                                 conn=conn)
            if turn_status:
                cur.execute(
                    "UPDATE ai_chat_turns SET status = %s, finished_at = NOW(), "
                    "last_event_at = NOW() "
                    "WHERE session_id = %s "
                    "  AND status IN ('accepted','running','recovering')",
                    (turn_status, session_id),
                )
        conn.commit()
    out = {'id': sid, 'batch_id': batch_id}
    out.update(batch or {})
    return out


def read_child_control_state(session_id: str) -> dict | None:
    """读子会话当前控制态（status/两标志/generation/fencing_token）。transition
    返回 None 时由调用方据此决定按 cancel/pause 落终态还是忽略（spec §6.1 表）。
    fencing_token 一并带出：重定向落终态时原样回传（自洽写回，M6）。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, cancel_requested, pause_requested, "
                "execution_generation, fencing_token FROM ai_chat_sessions "
                "WHERE id = %s",
                (session_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {'status': row[0], 'cancel_requested': row[1],
            'pause_requested': row[2], 'execution_generation': row[3],
            'fencing_token': row[4]}


def cancel_child(user_id: str, batch_id: str, session_id: str) -> dict | None:
    """Request cancellation of a single child session within a batch.

    - paused：同步 CAS 落成 'cancelled' 并计入 failed 聚合（F5：paused 子任务
      不在 worker 的任何扫描路径里，只置 flag 永远无人消费——必须在这里落终态）；
    - pending：worker 下个调度 tick 直接落 cancelled（不占并发槽位）；
    - running：worker 协作式打断（cancel_requested → 轮询发现 → abort）。

    条件 UPDATE 替代旧的先查后改（P0 spec §6.2：TOCTOU 修复）；终态判断以
    UPDATE 后回读为准。Returns the updated session dict, or None if not
    found/not owned. Raises ValueError if the child is already terminal.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            # paused → cancelled：CAS（仅 paused 行可命中），同事务计数+重算
            cur.execute(
                "UPDATE ai_chat_sessions s SET status = 'cancelled', "
                "  error_message = '已被调用方取消', cancel_requested = false, "
                "  active_turn_id = NULL "
                "FROM ai_chat_batches b "
                "WHERE s.batch_id = b.id AND s.id = %s AND s.batch_id = %s "
                "  AND b.user_id = %s AND s.status = 'paused' "
                "RETURNING s.id",
                (session_id, batch_id, user_id),
            )
            landed = cur.fetchone()
            if landed:
                cur.execute("UPDATE ai_chat_batches SET failed = failed + 1 "
                            "WHERE id = %s", (batch_id,))
                # conn+sid 入参：终态事件/outbox/effect 与取消同事务（H7）
                recompute_batch_status_tx(cur, batch_id, conn=conn,
                                          session_id=session_id)
            else:
                cur.execute(
                    "UPDATE ai_chat_sessions s SET cancel_requested = true "
                    "FROM ai_chat_batches b "
                    "WHERE s.batch_id = b.id AND s.id = %s AND s.batch_id = %s "
                    "  AND b.user_id = %s AND s.status IN ('pending', 'running') "
                    "RETURNING s.id",
                    (session_id, batch_id, user_id),
                )
                if not cur.fetchone():
                    # 没命中：要么不存在（Not owned/找不到），要么已终态。
                    cur.execute(
                        "SELECT s.status FROM ai_chat_sessions s "
                        "JOIN ai_chat_batches b ON s.batch_id = b.id "
                        "WHERE s.id = %s AND s.batch_id = %s AND b.user_id = %s",
                        (session_id, batch_id, user_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        conn.rollback()
                        return None
                    conn.rollback()
                    raise ValueError(f'子任务已结束（{row[0]}），无法取消')
        conn.commit()
    return get_child_session(user_id, batch_id, session_id)


def get_child_session(user_id: str, batch_id: str, session_id: str) -> dict | None:
    """Get a single child session within a batch."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.id, s.status, s.error_message, s.cancel_requested "
                "FROM ai_chat_sessions s "
                "JOIN ai_chat_batches b ON s.batch_id = b.id "
                "WHERE s.id = %s AND s.batch_id = %s AND b.user_id = %s",
                (session_id, batch_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {'id': row[0], 'status': row[1], 'error_message': row[2],
                    'cancel_requested': row[3]}


def reexecute_child(user_id: str, batch_id: str, session_id: str) -> dict | None:
    """Re-run a single TERMINAL (completed/failed) batch child from scratch:
    delete its old messages, reset it to pending with a cleared OpenCode session,
    roll back the batch counter, recompute status (-> running). Returns updated
    detail, or None if the child isn't found / not owned. Raises ValueError if the
    child is not in a terminal state."""
    with get_db() as conn:
        with conn.cursor() as cur:
            # M1（复核报告 §3）：与 continue_child 同款 FOR UPDATE + 条件
            # UPDATE + 预读状态计数回滚，消灭 check-then-act 窗口
            cur.execute(
                "SELECT s.status FROM ai_chat_sessions s "
                "JOIN ai_chat_batches b ON s.batch_id = b.id "
                "WHERE s.id = %s AND s.batch_id = %s AND b.user_id = %s",
                (session_id, batch_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            prev_status = row[0]
            if prev_status not in ('completed', 'failed', 'cancelled', 'needs_review'):
                raise ValueError('only completed/failed/cancelled/needs_review children can be re-executed')
            cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (session_id,))
            # 重新执行=全新一轮（2026-09-29）：清上一轮的变更登记/子代理
            # （含子代理消息）/工具调用账本/门禁期望——工具调用面板与终态
            # 核对都从零开始；期望由 worker 派发时按批定义重新登记。
            # 工作区清空重建由路由层 reset_session_workspace 完成。
            cur.execute("DELETE FROM ai_chat_session_files WHERE session_id = %s",
                        (session_id,))
            cur.execute(
                "DELETE FROM ai_chat_subtask_messages WHERE subtask_id IN "
                "  (SELECT id FROM ai_chat_subtasks WHERE root_session_id = %s)",
                (session_id,))
            cur.execute("DELETE FROM ai_chat_subtasks WHERE root_session_id = %s",
                        (session_id,))
            cur.execute("DELETE FROM agent_tool_calls WHERE root_session_id = %s",
                        (session_id,))
            cur.execute(
                "DELETE FROM action_expectations "
                "WHERE scope_type = 'session' AND scope_id = %s", (session_id,))
            # 复用锚点同步清除（2026-09-29）：重新执行=全新一轮，子代理会话
            # 也应从新开始——pin 保留会让新轮委派注入旧子会话（带着上一轮
            # 全部历史的上下文），模型可能直接复用旧结论而非真正重跑。
            cur.execute("DELETE FROM ai_subagent_pins WHERE root_session_id = %s",
                        (session_id,))
            cur.execute(
                "UPDATE ai_chat_sessions SET status='pending', opencode_session_id=NULL, "
                "  last_message_preview=NULL, error_message=NULL, cancel_requested=false, "
                "  pause_requested=false, gate_status=NULL, gate_error=NULL, "
                "  retry_count=0, continue_prompt=NULL, active_turn_id=NULL, "
                "  execution_generation = execution_generation + 1 "
                "WHERE id = %s AND status = %s",
                (session_id, prev_status),
            )
            if cur.rowcount != 1:
                conn.rollback()
                raise ValueError('子任务状态已变化，请刷新后重试')
            if prev_status == 'completed':
                cur.execute("UPDATE ai_chat_batches SET done = done - 1 WHERE id = %s", (batch_id,))
            else:
                # 'failed' 与 'cancelled' 都记在 failed 计数里（见
                # batch_engine._cancel_pending_requests / _mark_cancelled）。
                cur.execute("UPDATE ai_chat_batches SET failed = failed - 1 WHERE id = %s", (batch_id,))
        conn.commit()
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id)


def continue_child(user_id: str, batch_id: str, session_id: str,
                   prompt: str, repark: bool = False) -> dict | None:
    """Continue a TERMINAL (completed/failed) batch child with a new prompt,
    preserving conversation history and reusing the existing OpenCode session.

    `repark=True` 开启接管通道（spec D3）：PAUSED 子任务也准入——置
    continue_prompt 与 continue_repark=true 回 pending，不占 done/failed
    计数（paused 从未计入）；worker 完成后经 try_repark_transition 把控制
    权交还外部系统。repark=False 时 paused 子任务被拒绝：接管中的会话只能
    经接管通道续跑。

    Unlike reexecute_child: does NOT delete messages, does NOT null out
    opencode_session_id. Sets continue_prompt so the worker can pick it up
    and send the new prompt to the existing OpenCode session.

    Returns updated detail, or None if child not found / not owned.
    Raises ValueError if child is not in a terminal state (or paused without
    repark=True).
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.status FROM ai_chat_sessions s "
                "JOIN ai_chat_batches b ON s.batch_id = b.id "
                "WHERE s.id = %s AND s.batch_id = %s AND b.user_id = %s",
                (session_id, batch_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            # N1 修复（复核报告 §4.1）：计数回滚必须用「更新前」状态——
            # UPDATE RETURNING 返回的是更新后的 'pending'，用它判定会把
            # completed 子任务的 done-1 错做成 failed-1（failed 静默变负、
            # 批次终态算成 partial）。
            # 并发安全不用 FOR UPDATE（会与 claim_guard 的 FOR KEY SHARE
            # 测试基建互锁死等），靠 UPDATE 谓词 CAS 兜底：预读与 UPDATE
            # 之间状态若被并发改变，UPDATE 0 行 → 回滚报 409，计数绝不
            # 拿错状态回滚。
            prev_status = row[0]
            if prev_status == 'paused':
                # 接管通道（spec D3）：仅 repark=True 准入。paused 不占
                # done/failed 计数——无需回滚。置 continue_repark=true 让
                # worker 据此在完成时把控制权交还外部系统。
                if not repark:
                    raise ValueError('paused 子任务须经接管通道（repark）续跑')
                cur.execute(
                    "UPDATE ai_chat_sessions SET status='pending', "
                    "  continue_prompt=%s, error_message=NULL, cancel_requested=false, "
                    "  pause_requested=false, continue_repark=true, "
                    "  execution_generation = execution_generation + 1 "
                    "WHERE id = %s AND status = 'paused'",
                    (prompt, session_id))
                if cur.rowcount != 1:
                    conn.rollback()
                    raise ValueError('子任务状态已变化，请刷新后重试')
            else:
                if prev_status not in ('completed', 'failed', 'cancelled', 'needs_review'):
                    raise ValueError('only completed/failed/cancelled/needs_review children can be continued')
                cur.execute(
                    "UPDATE ai_chat_sessions SET status='pending', "
                    "  continue_prompt=%s, error_message=NULL, cancel_requested=false, "
                    "  pause_requested=false, continue_repark=false, "
                    "  execution_generation = execution_generation + 1 "
                    "WHERE id = %s AND status = %s",
                    (prompt, session_id, prev_status),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    raise ValueError('子任务状态已变化，请刷新后重试')
                if prev_status == 'completed':
                    cur.execute("UPDATE ai_chat_batches SET done = done - 1 WHERE id = %s", (batch_id,))
                else:
                    # 'failed' 与 'cancelled' 都记在 failed 计数里。
                    cur.execute("UPDATE ai_chat_batches SET failed = failed - 1 WHERE id = %s", (batch_id,))
        conn.commit()
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id)


def resume_child(user_id: str, batch_id: str, session_id: str) -> dict | None:
    """Resume a single PAUSED batch child in place ("continue this one"):
    back to pending, and if it had already started (opencode_session_id set)
    inject RESUME_CONTINUE_PROMPT so the worker continues the existing
    OpenCode session/workspace instead of restarting from scratch.

    Other paused/cancelled children stay parked — unlike resume_batch, this
    never touches them. Paused children occupy no done/failed counter, so no
    counter rollback is needed. Raises ValueError if the child isn't paused.

    SET 恒清 continue_repark（spec D3）：接管轮可能被协作暂停回 paused 且带
    残留标志——不清的话 worker 会在完成后误走接管收口重停。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            # M1（复核报告 §3）：FOR UPDATE + 条件 UPDATE，消灭 check-then-act 窗口
            cur.execute(
                "SELECT s.status FROM ai_chat_sessions s "
                "JOIN ai_chat_batches b ON s.batch_id = b.id "
                "WHERE s.id = %s AND s.batch_id = %s AND b.user_id = %s",
                (session_id, batch_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            if row[0] != 'paused':
                raise ValueError('只有已暂停的子任务可以单独继续')
            cur.execute(
                "UPDATE ai_chat_sessions SET status='pending', error_message=NULL, "
                "  cancel_requested=false, pause_requested=false, continue_repark=false, "
                "  execution_generation = execution_generation + 1, "
                "  continue_prompt = CASE WHEN opencode_session_id IS NOT NULL "
                "                        THEN %s ELSE NULL END "
                "WHERE id = %s AND status = 'paused'",
                (RESUME_CONTINUE_PROMPT, session_id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                raise ValueError('子任务状态已变化，请刷新后重试')
        conn.commit()
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id)


def try_repark_transition(session_id: str, *, generation: int,
                          fencing_token: int) -> dict | None:
    """接管续跑收口（spec D3）：continue_repark=true 的 running 子会话回
    paused，不动 done/failed 计数，把控制权交还外部系统。CAS 未命中返回
    None——含「本就不是接管轮」，调用方（batch_engine）据此回落常规终态
    路径，无需预读标志（check-then-act 免除）。"""
    return transition_child(session_id, 'paused', generation=generation,
                            expect_pause=None, expect_repark=True,
                            turn_status='paused', fencing_token=fencing_token)


def admin_soft_delete_child(batch_id: str, sid: str) -> dict | None:
    """管理员软删除子任务:置 deleted_at(数据保留),前台批次详情/侧栏不再
    显示;同步按未删除子任务重算 total/done/failed 并刷新批次状态。

    仅终态(completed/failed/cancelled)子任务可删——运行/待运行与 worker
    冲突,需先取消。返回 {'seq','status','deletedAt'};
    子任务不存在返回 None;非终态/已删除抛 ValueError。"""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT status, batch_seq, deleted_at FROM ai_chat_sessions "
                "WHERE id = %s AND batch_id = %s",
                (sid, batch_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            if row['deleted_at'] is not None:
                raise ValueError('该子任务已删除')
            if row['status'] not in ('completed', 'failed', 'cancelled'):
                raise ValueError('仅已完成/失败/已取消的子任务可删除;'
                                 '运行中或待运行的请先取消')
            cur.execute(
                "UPDATE ai_chat_sessions SET deleted_at = now() "
                "WHERE id = %s AND batch_id = %s RETURNING deleted_at",
                (sid, batch_id),
            )
            deleted_at = cur.fetchone()['deleted_at']
            # 计数以“未删除”子任务为准重算,避免侧栏计数与可见行数不一致;
            # cancelled 与既有聚合约定一致计入 failed 侧(见 _mark_cancelled)
            cur.execute(
                "SELECT count(*) AS total, "
                "       count(*) FILTER (WHERE status = 'completed') AS done, "
                "       count(*) FILTER (WHERE status IN ('failed','cancelled')) AS failed "
                "  FROM ai_chat_sessions "
                " WHERE batch_id = %s AND deleted_at IS NULL",
                (batch_id,),
            )
            counts = cur.fetchone()
            cur.execute(
                "UPDATE ai_chat_batches SET total = %s, done = %s, failed = %s "
                "WHERE id = %s",
                (counts['total'], counts['done'], counts['failed'], batch_id),
            )
        conn.commit()
    from ai.batch.engine import _recompute_batch_status
    # H7④（12 号 §4.1）：admin 软删重算有刚转移的 sid 可锚定——真触发
    # 终态回调时 effect 落在该子会话上，不靠 tiebreaker 兜底
    _recompute_batch_status(batch_id, anchor_sid=sid)
    return {'seq': row['batch_seq'], 'status': row['status'],
            'deletedAt': deleted_at.isoformat() if deleted_at else None}



__all__ = [
    '_PREVIEW_UNSET',
    'admin_soft_delete_child',
    'cancel_child',
    'continue_child',
    'get_batch_detail',
    'get_child_session',
    'get_db',
    'read_child_control_state',
    'RealDictCursor',
    'recompute_batch_status_tx',
    '_recompute_batch_status_for',
    'RESUME_CONTINUE_PROMPT',
    'reexecute_child',
    'resume_child',
    'transition_child',
    'try_repark_transition',
]
