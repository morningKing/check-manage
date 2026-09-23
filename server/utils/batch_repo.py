"""DB layer for ai_chat_batches and their child ai_chat_sessions rows.

Routes are thin; this module owns the SQL.
"""
import uuid
from psycopg2.extras import Json, RealDictCursor

from db import get_db


MAX_FILES_PER_BATCH = 50

# transition_child 的「未传 preview」哨兵：传了 None 表示显式清空，
# 不传表示保持原值（P0 spec §6.1 的 COALESCE 语义由调用方区分）。
_PREVIEW_UNSET = object()

# 批次终态（cancel/计数落定后重算用；'paused' 是非终态中间态）
BATCH_TERMINAL_STATUSES = ('completed', 'partial', 'failed')


def recompute_batch_status_tx(cur, batch_id: str) -> dict:
    """在同一事务/游标里重算批次状态并取回（status + 回调字段）。
    transition_child 用；引擎的 _recompute_batch_status（带回调通知）保持独立。"""
    cur.execute("SELECT done, failed, total FROM ai_chat_batches WHERE id=%s",
                (batch_id,))
    row = cur.fetchone()
    if not row:
        return {}
    done, failed, total = row
    cur.execute(
        "SELECT count(*) FROM ai_chat_sessions WHERE batch_id=%s AND status='paused'",
        (batch_id,))
    paused = cur.fetchone()[0]
    if done + failed == 0 and paused == 0:
        status = 'pending'
    elif done + failed < total and paused > 0:
        status = 'paused'
    elif done + failed < total:
        status = 'running'
    elif failed == total:
        status = 'failed'
    elif done == total:
        status = 'completed'
    else:
        status = 'partial'
    cur.execute(
        "UPDATE ai_chat_batches SET status=%s, "
        "completed_at = CASE WHEN %s = total THEN now() ELSE NULL END "
        "WHERE id=%s RETURNING status, done, failed, total, "
        "callback_url, callback_secret",
        (status, done + failed, batch_id))
    return dict(zip(('status', 'done', 'failed', 'total',
                     'callback_url', 'callback_secret'), cur.fetchone()))


def transition_child(session_id: str, new_status: str, *, generation: int,
                     error_message: str | None = None,
                     last_message_preview=_PREVIEW_UNSET,
                     gate_status: str | None = None,
                     gate_error: str | None = None,
                     expect_cancel: bool | None = False,
                     expect_pause: bool | None = False,
                     count: str | None = None,
                     turn_status: str | None = None) -> dict | None:
    """P0 CAS 终态转移（ai-harness-p0 spec §6.1）——终态写入的唯一入口。

    WHERE 带 status='running' + execution_generation + cancel/pause 标志期望；
    rowcount=0 即写回过期（stale worker / 已被并发收口），返回 None，调用方
    不得改状态、计数或消息。rowcount=1 时在同一事务内完成三件事再提交：
    批次计数 +1（count='done'/'failed'）→ 批次状态重算 → active turn 收口，
    消灭「计数已加、状态未算」的中间态。

    `generation` 是 claim 时捕获的 execution_generation；不匹配即 0 行。
    expect_cancel/expect_pause 三态：False=要求未置位（done/failed/paused）、
    True=要求已置位（cancelled）、None=不关心（cancel 优先语义下，取消收口
    不应再被并存的 pause 标志挡住）。
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
                RETURNING id, batch_id
                """,
                (new_status, error_message, preview, gate_status, gate_error,
                 gate_status, session_id, generation,
                 expect_cancel, bool(expect_cancel),
                 expect_pause, bool(expect_pause)),
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
            batch = recompute_batch_status_tx(cur, batch_id) if batch_id else {}
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
    """读子会话当前控制态（status/两标志/generation）。transition 返回 None 时
    由调用方据此决定按 cancel/pause 落终态还是忽略（spec §6.1 表）。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, cancel_requested, pause_requested, "
                "execution_generation FROM ai_chat_sessions WHERE id = %s",
                (session_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {'status': row[0], 'cancel_requested': row[1],
            'pause_requested': row[2], 'execution_generation': row[3]}


def get_max_files_per_batch() -> int:
    """批任务子会话个数上限（可配置）。

    每个输入文件对应一个子会话，上限存于 ai_settings.max_batch_sessions，
    管理员在 AI 配置页调整。读取失败、未配置或非法值回落到默认
    MAX_FILES_PER_BATCH；最小 1。
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT max_batch_sessions FROM ai_settings WHERE id = 1')
            row = cur.fetchone()
        val = int(row[0]) if row and row[0] is not None else MAX_FILES_PER_BATCH
    except Exception:
        return MAX_FILES_PER_BATCH
    return max(1, val)


def create_batch(user_id: str, *, name: str, prompt: str,
                 template_id: str | None, files: list[dict],
                 scan_task_id: str | None = None,
                 agent: str | None = None,
                 model: str | None = None,
                 provision_repo: str | None = None,
                 provision_ref: str | None = None,
                 action_checks: list | None = None,
                 gate_retry: bool | None = None,
                 api_key_id: str | None = None,
                 callback_url: str | None = None,
                 callback_secret: str | None = None) -> dict:
    """Atomically insert a batch + N child sessions.

    `files` is a list of {name, path} dicts where `path` is workspace-relative
    (under batch-staging/...). Each entry may also carry an optional `recordId`
    key: the source record id, stamped into `source_record_id` for scan tasks.
    `scan_task_id` is an optional param linking the batch and its child sessions
    to a scan task (stamped on both the batch row and every child session row —
    the batch-level copy lets the AI 助手 sidebar group scan-task batches apart
    from user-created ones without joining to sessions).
    `agent` is an optional OpenCode agent name to use for this batch.
    `model` is an optional "<providerID>/<modelID>" to run this batch with;
    empty falls back to the global OPENCODE_MODEL / the agent's default.
    `api_key_id` stamps the source API key (open API callers) that created this
    batch; None for UI/scan-task created batches.
    `callback_url`/`callback_secret` (open API only): when set, batch_engine
    POSTs an HMAC-signed completion notification to `callback_url` once the
    batch reaches a terminal status, instead of requiring the caller to poll.
    Returns {batch, sessions}.
    """
    # 0 文件合法:空批壳,后续 append 填充
    max_files = get_max_files_per_batch()
    if len(files) > max_files:
        raise ValueError(f"max {max_files} files per batch")

    batch_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO ai_chat_batches "
                "  (id, user_id, name, prompt, template_id, total, status, agent, model, "
                "   provision_repo, provision_ref, api_key_id, callback_url, callback_secret, "
                "   scan_task_id, action_checks, gate_retry) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (batch_id, user_id, name, prompt, template_id, len(files), agent, model,
                 provision_repo, provision_ref, api_key_id, callback_url, callback_secret,
                 scan_task_id,
                 Json(action_checks) if action_checks else None,
                 gate_retry),
            )
            batch = dict(cur.fetchone())

            sessions = []
            for seq, f in enumerate(files):
                sid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, status, batch_id, batch_seq, batch_input_file, "
                    "   scan_task_id, source_record_id) "
                    "VALUES (%s, %s, 'pending', %s, %s, %s, %s, %s) RETURNING *",
                    (sid, user_id, batch_id, seq, f['path'],
                     scan_task_id, f.get('recordId')),
                )
                sessions.append(dict(cur.fetchone()))
        conn.commit()
    return {'batch': batch, 'sessions': sessions}


def list_batches(user_id: str, *, page: int, page_size: int,
                 api_key_id: str | None = None) -> dict:
    """列出批任务。

    `api_key_id` 非 None 时**再**按来源密钥圈一层 —— 对外 API 要求严格按密钥隔离。
    传 None 时行为与加这个参数之前完全一致，故 UI / 扫描任务 / 行操作等既有调用点无需改动。
    """
    offset = (page - 1) * page_size
    scope = "WHERE user_id = %s"
    base = [user_id]
    if api_key_id is not None:
        scope += " AND api_key_id = %s"
        base.append(api_key_id)
    # cancelled / paused 计数：failed 里混着 cancelled（取消也计入 failed 聚合），
    # UI 需要「继续运行」按钮的显隐判据 —— cancelled/paused 都可被 resume。
    cancelled_sub = ("(SELECT count(*) FROM ai_chat_sessions s "
                     " WHERE s.batch_id = ai_chat_batches.id AND s.status = 'cancelled')")
    paused_sub = ("(SELECT count(*) FROM ai_chat_sessions s "
                  " WHERE s.batch_id = ai_chat_batches.id AND s.status = 'paused')")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT *, {cancelled_sub} AS cancelled, {paused_sub} AS paused "
                f"FROM ai_chat_batches {scope} "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (*base, page_size, offset),
            )
            items = [dict(r) for r in cur.fetchall()]
            cur.execute(f"SELECT count(*) AS n FROM ai_chat_batches {scope}", tuple(base))
            total = cur.fetchone()['n']
    return {'items': items, 'total': total, 'page': page, 'pageSize': page_size}


def get_batch_detail(user_id: str, batch_id: str, *,
                     api_key_id: str | None = None) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            sql = (
                "SELECT *, "
                "  (SELECT count(*) FROM ai_chat_sessions s "
                "   WHERE s.batch_id = ai_chat_batches.id AND s.status='cancelled') AS cancelled, "
                "  (SELECT count(*) FROM ai_chat_sessions s "
                "   WHERE s.batch_id = ai_chat_batches.id AND s.status='paused') AS paused "
                "FROM ai_chat_batches WHERE id=%s AND user_id=%s"
            )
            params = [batch_id, user_id]
            if api_key_id is not None:
                sql += " AND api_key_id = %s"
                params.append(api_key_id)
            cur.execute(sql, tuple(params))
            batch = cur.fetchone()
            if not batch:
                return None
            cur.execute(
                "SELECT id, status, batch_seq, batch_input_file, workspace_path, "
                "       opencode_session_id, error_message, last_message_preview, "
                "       (SELECT count(*) FROM action_expectations e "
                "        WHERE e.scope_id = ai_chat_sessions.id "
                "          AND e.last_status = 'failed') AS gate_failed, "
                "       (SELECT count(*) FROM action_expectations e "
                "        WHERE e.scope_id = ai_chat_sessions.id "
                "          AND e.last_status = 'passed') AS gate_passed "
                "FROM ai_chat_sessions "
                "WHERE batch_id=%s AND deleted_at IS NULL ORDER BY batch_seq",
                (batch_id,),
            )
            sessions = [dict(r) for r in cur.fetchall()]
    return {'batch': dict(batch), 'sessions': sessions}


def delete_batch(user_id: str, batch_id: str, *,
                 api_key_id: str | None = None) -> bool:
    """Returns True if deleted, False if not found.

    Callers MUST run per-session workspace cleanup BEFORE invoking this for
    children that have a workspace_path. See routes for the orchestration.

    `api_key_id` non-None additionally scopes the delete to that source key.
    """
    sql = "DELETE FROM ai_chat_batches WHERE id=%s AND user_id=%s"
    params = [batch_id, user_id]
    if api_key_id is not None:
        sql += " AND api_key_id = %s"
        params.append(api_key_id)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def cancel_batch(user_id: str, batch_id: str, *,
                 api_key_id: str | None = None) -> dict | None:
    """Request cancellation of every pending/running child of a batch.

    Pending children are converted to 'cancelled' on the worker's next
    dispatch tick without ever running (batch_engine._cancel_pending_requests);
    running children are cooperatively aborted mid-poll
    (batch_engine.BatchWorker._await_finished). This function only flips the
    cancel_requested flag — it does not itself touch status/counters, since
    the worker owns every status transition (touching it here too would race
    the worker's own claim/poll cycle).

    Raises ValueError if the batch is already terminal (nothing to cancel).
    Returns updated batch detail, or None if not found / not owned.

    取消不是终局：resume_batch 可以把 cancelled 子任务恢复继续执行。
    对已暂停（paused）的子任务直接落成 cancelled（它们不在 worker 的轮询里，
    只能在路由层同步翻状态），并计入 failed 聚合。
    """
    sql = "SELECT status FROM ai_chat_batches WHERE id=%s AND user_id=%s"
    params = [batch_id, user_id]
    if api_key_id is not None:
        sql += " AND api_key_id = %s"
        params.append(api_key_id)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
            if not row:
                return None
            if row[0] in ('completed', 'partial', 'failed'):
                raise ValueError('批任务已结束，无法取消')
            cur.execute(
                "UPDATE ai_chat_sessions SET cancel_requested = true "
                "WHERE batch_id = %s AND status IN ('pending', 'running')",
                (batch_id,),
            )
            # 已暂停的子任务不在 worker 的任何扫描路径里（不 claim、不轮询），
            # 中断要在这里同步落成 cancelled。
            cur.execute(
                "UPDATE ai_chat_sessions SET status = 'cancelled', "
                "  error_message = '已被调用方取消' "
                "WHERE batch_id = %s AND status = 'paused'",
                (batch_id,),
            )
            paused_n = cur.rowcount
            if paused_n:
                cur.execute(
                    "UPDATE ai_chat_batches SET failed = failed + %s WHERE id = %s",
                    (paused_n, batch_id),
                )
        conn.commit()
    if paused_n:
        _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id, api_key_id=api_key_id)


def pause_batch(user_id: str, batch_id: str, *,
                api_key_id: str | None = None) -> dict | None:
    """暂停整批仍在排队/运行中的子任务（cancel_requested 的姊妹机制）：

    对 pending/running 的子任务置 pause_requested=true；worker 在下一个调度
    tick（排队的）或轮询周期（运行中的，先 abort OpenCode 回合）把它们落到
    非终态 'paused' —— 不占 failed 计数。之后 resume_batch 可以从原 OpenCode
    会话/工作区续跑（与 cancelled 的 resume 共用同一条路）。

    批次状态立即置 'paused'（给界面即时反馈）；子任务逐个翻成 paused 后，
    _recompute_batch_status 的 paused 计数规则会保持这个状态。

    Raises ValueError if the batch is already terminal or has nothing to pause.
    Returns updated batch detail, or None if not found / not owned.
    """
    sql = "SELECT status FROM ai_chat_batches WHERE id=%s AND user_id=%s"
    params = [batch_id, user_id]
    if api_key_id is not None:
        sql += " AND api_key_id = %s"
        params.append(api_key_id)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
            if not row:
                return None
            if row[0] in ('completed', 'partial', 'failed'):
                raise ValueError('批任务已结束，无法暂停')
            cur.execute(
                "UPDATE ai_chat_sessions SET pause_requested = true "
                "WHERE batch_id = %s AND status IN ('pending', 'running')",
                (batch_id,),
            )
            if cur.rowcount == 0:
                raise ValueError('批任务没有排队或运行中的子任务，无需暂停')
            cur.execute("UPDATE ai_chat_batches SET status = 'paused' WHERE id = %s",
                        (batch_id,))
        conn.commit()
    return get_batch_detail(user_id, batch_id, api_key_id=api_key_id)


# resume_batch 给"停止前已经开跑过"的子任务注入的续跑提示词。措辞要点：
# 明确这是从中断处继续（不是重新开始），并让它先核对工作区/上下文现状。
RESUME_CONTINUE_PROMPT = (
    '上一轮执行被中断。请先查看工作区现状与已有对话上下文，'
    '从中断处继续完成原任务；已完成的部分不要重做，最终给出完整结果。'
)


def resume_batch(user_id: str, batch_id: str, *,
                 api_key_id: str | None = None) -> dict | None:
    """把已停止（cancelled）/已暂停（paused）的子任务恢复为 pending 继续执行——
    「停止后在原来的工作上继续」：

      - 停止/暂停前已开跑（opencode_session_id 非空）：置 continue_prompt，worker
        走 continue 模式，在原 OpenCode 会话/原工作区上续跑，保留全部历史；
      - 排队中被停止/暂停（还没开跑）：正常全新执行原任务。

    只动 cancelled/paused 子任务；failed 的仍走 reset_failed_to_pending（重试）。
    计数回滚只针对 cancelled（paused 从未计入 failed）。重算批次状态
    （paused/terminal → running），调用方负责唤醒 worker。

    Returns updated batch detail, or None if not found / not owned.
    Raises ValueError if the batch has nothing resumable.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            sql = "SELECT id FROM ai_chat_batches WHERE id=%s AND user_id=%s"
            params = [batch_id, user_id]
            if api_key_id is not None:
                sql += " AND api_key_id = %s"
                params.append(api_key_id)
            cur.execute(sql, tuple(params))
            if not cur.fetchone():
                return None
            # 回滚量要先数：UPDATE 会把行翻成 pending，之后按状态数不到了。
            cur.execute(
                "SELECT count(*) FROM ai_chat_sessions "
                "WHERE batch_id = %s AND status = 'cancelled'",
                (batch_id,),
            )
            cancelled_n = cur.fetchone()[0]
            cur.execute(
                "UPDATE ai_chat_sessions "
                "SET status='pending', error_message=NULL, cancel_requested=false, "
                "    pause_requested=false, execution_generation = execution_generation + 1, "
                "    continue_prompt = CASE WHEN opencode_session_id IS NOT NULL "
                "                           THEN %s ELSE NULL END "
                "WHERE batch_id = %s AND status IN ('cancelled', 'paused')",
                (RESUME_CONTINUE_PROMPT, batch_id),
            )
            count = cur.rowcount
            if count and cancelled_n:
                cur.execute(
                    "UPDATE ai_chat_batches SET failed = failed - %s WHERE id = %s",
                    (cancelled_n, batch_id),
                )
        conn.commit()
    if not count:
        raise ValueError('该批次没有已暂停或已中断的子任务，无需继续')
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id, api_key_id=api_key_id)


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
                recompute_batch_status_tx(cur, batch_id)
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


def append_to_batch(user_id: str, batch_id: str, files: list[dict], *,
                    api_key_id: str | None = None) -> dict | None:
    """Append N child sessions to an existing batch (any status). seq continues
    from max+1, total += N, status recomputed (-> running). Returns
    {batch, sessions} or None if the batch isn't found / not owned.

    `api_key_id` non-None additionally scopes the append to that source key, so
    the external API can't grow a batch that a different key (or the UI) created.
    """
    if not files:
        raise ValueError("at least one file required")
    sql = "SELECT total FROM ai_chat_batches WHERE id=%s AND user_id=%s"
    params = [batch_id, user_id]
    if api_key_id is not None:
        sql += " AND api_key_id = %s"
        params.append(api_key_id)
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 行锁串行化同一批次的并发 append（F6）：MAX(batch_seq)+1 只在
            # 持有批次行锁的事务里执行，两个并发 append 不再算出同一个序号；
            # UNIQUE(batch_id, batch_seq)（P0 迁移）作为最后防线。
            cur.execute("SELECT id, total FROM ai_chat_batches "
                        "WHERE id=%s AND user_id=%s FOR UPDATE",
                        (batch_id, user_id))
            row = cur.fetchone()
            if not row:
                return None
            total_now = row['total']
            if api_key_id is not None:
                cur.execute("SELECT 1 FROM ai_chat_batches WHERE id=%s AND api_key_id=%s",
                            (batch_id, api_key_id))
                if not cur.fetchone():
                    return None
            max_files = get_max_files_per_batch()
            if total_now + len(files) > max_files:
                raise ValueError(f"max {max_files} files per batch")
            cur.execute("SELECT COALESCE(MAX(batch_seq), -1) AS m "
                        "FROM ai_chat_sessions WHERE batch_id=%s", (batch_id,))
            start = cur.fetchone()['m'] + 1
            sessions = []
            for i, f in enumerate(files):
                sid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                    "VALUES (%s, %s, 'pending', %s, %s, %s) RETURNING *",
                    (sid, user_id, batch_id, start + i, f['path']),
                )
                sessions.append(dict(cur.fetchone()))
            cur.execute("UPDATE ai_chat_batches SET total = total + %s WHERE id=%s",
                        (len(files), batch_id))
        conn.commit()
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id, api_key_id=api_key_id)


def _recompute_batch_status_for(batch_id: str) -> None:
    """Local SQL equivalent of batch_engine._recompute_batch_status to avoid
    circular imports (batch_engine imports batch_repo).

    与引擎侧同一条 paused 规则：还有 paused 子任务且未全部终态 → 'paused'。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT done, failed, total, "
                "       (SELECT count(*) FROM ai_chat_sessions s "
                "         WHERE s.batch_id = ai_chat_batches.id "
                "           AND s.status = 'paused') "
                "FROM ai_chat_batches WHERE id=%s", (batch_id,))
            row = cur.fetchone()
            if not row:
                return
            done, failed, total, paused = row
            terminal = done + failed
            if terminal == 0 and paused == 0:
                status = 'pending'
            elif terminal < total and paused > 0:
                status = 'paused'
            elif terminal < total:
                status = 'running'
            elif failed == total:
                status = 'failed'
            elif done == total:
                status = 'completed'
            else:
                status = 'partial'
            cur.execute("UPDATE ai_chat_batches SET status=%s, "
                        "completed_at = CASE WHEN %s = total THEN now() ELSE NULL END "
                        "WHERE id=%s", (status, terminal, batch_id))
        conn.commit()


def reexecute_child(user_id: str, batch_id: str, session_id: str) -> dict | None:
    """Re-run a single TERMINAL (completed/failed) batch child from scratch:
    delete its old messages, reset it to pending with a cleared OpenCode session,
    roll back the batch counter, recompute status (-> running). Returns updated
    detail, or None if the child isn't found / not owned. Raises ValueError if the
    child is not in a terminal state."""
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
            status = row[0]
            if status not in ('completed', 'failed', 'cancelled'):
                raise ValueError('only completed/failed/cancelled children can be re-executed')
            cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (session_id,))
            cur.execute(
                "UPDATE ai_chat_sessions SET status='pending', opencode_session_id=NULL, "
                "  last_message_preview=NULL, error_message=NULL, cancel_requested=false, "
                "  pause_requested=false, gate_status=NULL, gate_error=NULL, "
                "  execution_generation = execution_generation + 1 "
                "WHERE id = %s",
                (session_id,),
            )
            if status == 'completed':
                cur.execute("UPDATE ai_chat_batches SET done = done - 1 WHERE id = %s", (batch_id,))
            else:
                # 'failed' 与 'cancelled' 都记在 failed 计数里（见
                # batch_engine._cancel_pending_requests / _mark_cancelled）。
                cur.execute("UPDATE ai_chat_batches SET failed = failed - 1 WHERE id = %s", (batch_id,))
        conn.commit()
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id)


def continue_child(user_id: str, batch_id: str, session_id: str,
                   prompt: str) -> dict | None:
    """Continue a TERMINAL (completed/failed) batch child with a new prompt,
    preserving conversation history and reusing the existing OpenCode session.

    Unlike reexecute_child: does NOT delete messages, does NOT null out
    opencode_session_id. Sets continue_prompt so the worker can pick it up
    and send the new prompt to the existing OpenCode session.

    Returns updated detail, or None if child not found / not owned.
    Raises ValueError if child is not in a terminal state.
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
            status = row[0]
            if status not in ('completed', 'failed', 'cancelled'):
                raise ValueError('only completed/failed/cancelled children can be continued')
            cur.execute(
                "UPDATE ai_chat_sessions SET status='pending', "
                "  continue_prompt=%s, error_message=NULL, cancel_requested=false, "
                "  pause_requested=false, execution_generation = execution_generation + 1 "
                "WHERE id = %s",
                (prompt, session_id),
            )
            if status == 'completed':
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
            if row[0] != 'paused':
                raise ValueError('只有已暂停的子任务可以单独继续')
            cur.execute(
                "UPDATE ai_chat_sessions SET status='pending', error_message=NULL, "
                "  cancel_requested=false, pause_requested=false, "
                "  execution_generation = execution_generation + 1, "
                "  continue_prompt = CASE WHEN opencode_session_id IS NOT NULL "
                "                        THEN %s ELSE NULL END "
                "WHERE id = %s",
                (RESUME_CONTINUE_PROMPT, session_id),
            )
        conn.commit()
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id)


_UNSET = object()


def update_batch_config(user_id: str, batch_id: str, *,
                        agent: str | None, model: str | None,
                        provision_repo: str | None = None,
                        provision_ref: str | None = None,
                        action_checks= _UNSET,
                        gate_retry= _UNSET,
                        api_key_id: str | None = None,
                        callback_url: str | None = None,
                        callback_secret: str | None = None) -> dict | None:
    """Update a batch's agent/model/provision repo/callback (owner-only). This
    is a full-replace, not a partial patch: every column here is set to
    whatever was passed, so an omitted field (default None) is cleared to the
    default — callers must resend a field to keep it. Returns updated detail,
    or None if not found / not owned. Takes effect on the next run the worker
    claims (retry / reexecute / pending), since the worker reads these fresh
    per run via _fetch_batch_context.

    `api_key_id` non-None additionally scopes the update to that source key.
    """
    # 归属预检：action_checks/gate_retry 的子更新与主更新同口径——api_key_id
    # 非 None 时必须命中该密钥名下的批次，否则什么也不写（防跨密钥越权改门禁）。
    scope_sql = "SELECT id FROM ai_chat_batches WHERE id = %s AND user_id = %s"
    scope_params = [batch_id, user_id]
    if api_key_id is not None:
        scope_sql += " AND api_key_id = %s"
        scope_params.append(api_key_id)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(scope_sql, tuple(scope_params))
            if not cur.fetchone():
                return None
    if action_checks is not _UNSET:
        # 动作门禁期望(编辑入口,设计 §5.2 入口 A):显式传入才更新,
        # 未传保持原值——既有调用方(旧 UI/开放 API)不受影响。
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_batches SET action_checks = %s "
                    "WHERE id = %s AND user_id = %s",
                    (Json(action_checks) if action_checks else None,
                     batch_id, user_id),
                )
            conn.commit()
    if gate_retry is not _UNSET:
        # 修正开关(设计 §5.4):批级覆盖;None=清除覆盖,跟随全局环境变量
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_batches SET gate_retry = %s "
                    "WHERE id = %s AND user_id = %s",
                    (gate_retry, batch_id, user_id),
                )
            conn.commit()

    sql = ("UPDATE ai_chat_batches SET agent = %s, model = %s, "
           "  provision_repo = %s, provision_ref = %s, "
           "  callback_url = %s, callback_secret = %s "
           "WHERE id = %s AND user_id = %s")
    params = [agent, model, provision_repo, provision_ref,
             callback_url, callback_secret, batch_id, user_id]
    if api_key_id is not None:
        sql += " AND api_key_id = %s"
        params.append(api_key_id)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            updated = cur.rowcount > 0
        conn.commit()
    if not updated:
        return None
    return get_batch_detail(user_id, batch_id)


def reset_failed_to_pending(user_id: str, batch_id: str, *,
                            api_key_id: str | None = None) -> int:
    """Returns count of sessions reset. Also clears batch.failed counter and
    recomputes batch.status.

    `api_key_id` non-None additionally scopes the reset to that source key.
    """
    owner_scope = "SELECT id FROM ai_chat_batches WHERE user_id=%s"
    owner_params = [batch_id, user_id]
    if api_key_id is not None:
        owner_scope += " AND api_key_id = %s"
        owner_params.append(api_key_id)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_chat_sessions "
                "SET status='pending', error_message=NULL, "
                "    cancel_requested=false, pause_requested=false, "
                "    execution_generation = execution_generation + 1 "
                "WHERE batch_id=%s AND status='failed' "
                f"  AND batch_id IN ({owner_scope})",
                tuple(owner_params),
            )
            count = cur.rowcount
            # 回滚 failed 计数（被重试的子任务此前计在 failed 里）+ F7：批次
            # 状态走统一重算（含 paused 规则），不再内联 CASE 硬算——有 paused
            # 子任务时旧写法会把本应 'paused' 的批次错写成 'pending'。
            if count:
                cur.execute("UPDATE ai_chat_batches SET failed = failed - %s "
                            "WHERE id = %s", (count, batch_id))
                recompute_batch_status_tx(cur, batch_id)
            conn.commit()
    return count


def get_batch_results(batch_id: str) -> list[dict]:
    """按 batch_seq 顺序返回每个子任务的结果。

    `output` **只在子任务 status == 'completed' 时返回**，其余状态一律 None。
    这是对外契约的一部分（见 docs/user-guide/integration/ai-batch-api.md 4.5/9.2），
    也是一道必须的安全门：batch_engine._run_one 会以 PROGRESS_PERSIST_SEC 为周期
    在会话**运行期间**反复调 _persist_conversation 落库（为了让界面能实时看到
    对话），所以 running 的子任务、以及跑到一半超时被标 failed 的子任务，
    ai_chat_messages 里都已经躺着**半截 AI 文本**。不设这道门，集成方会按文档
    推荐的「终态后取结果」流程拿到被截断的输出并写进下游业务系统。

    `output` 取该子会话**最后一条 assistant 消息**里全部 text 片段的拼接，
    而不是 ai_chat_sessions.last_message_preview —— 后者存的是
    batch_engine._preview_from() 取的第一行，读它会让调用方拿到被截断的输出。
    「最后一条」按 `ai_chat_messages.seq`（自增，插入即定序）排序，不用
    `created_at` —— batch_engine._persist_conversation 在同一事务里循环插入
    多条 assistant 行，PG 的 now()/DEFAULT NOW() 在同一事务内是事务级常量，
    同一事务插入的所有行 created_at 完全相同，排序不确定。

    `name` 取 batch_input_file 的 basename：该列存的是工作区相对路径
    （batch-staging/<userId>/<uploadId>/<文件名>），直接返回会泄漏内部 userId。
    """
    sql = """
        SELECT s.id, s.batch_input_file, s.status, s.error_message,
               (SELECT m.content FROM ai_chat_messages m
                 WHERE m.session_id = s.id AND m.role = 'assistant'
                 ORDER BY m.seq DESC LIMIT 1) AS content
          FROM ai_chat_sessions s
         WHERE s.batch_id = %s AND s.deleted_at IS NULL
         ORDER BY s.batch_seq
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (batch_id,))
            rows = [dict(r) for r in cur.fetchall()]

    results = []
    for r in rows:
        raw = r.get('batch_input_file') or ''
        name = raw.replace('\\', '/').rsplit('/', 1)[-1]
        completed = r['status'] == 'completed'
        results.append({
            'name': name,
            'status': r['status'],
            'output': _text_from_content(r.get('content')) if completed else None,
            'error': r.get('error_message'),
            'usage': get_session_usage(r['id']),
        })
    return results


def get_session_usage(session_id: str) -> dict | None:
    """Aggregate one session's per-message `meta` (durationMs/tokensInput/
    tokensOutput/cost — stamped by batch_engine._persist_conversation via
    utils.ai_message_meta.meta_from_info/public_meta, one row per OpenCode
    assistant message) into a single usage summary for the external API.

    Same combination rule as ai_message_meta.aggregate_metas (tokensInput
    takes the max, not the sum — OpenCode resends the growing context on
    every message, so summing would double-count; tokensOutput/cost/
    durationMs sum since each message is genuinely separate model output).
    We can't reuse aggregate_metas directly: it needs the `_created`/
    `_completed` timestamps to compute a true start→finish span, but
    `public_meta` strips those before persisting (by design — they're not
    part of the documented per-message shape), so `durationMs` here is a sum
    of each message's own already-computed duration instead of a span.

    Returns None when the session has no usable meta yet (still pending/
    running, or every turn ended before a measurable finish).
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT meta FROM ai_chat_messages "
                "WHERE session_id = %s AND role = 'assistant' AND meta IS NOT NULL",
                (session_id,),
            )
            metas = [r[0] for r in cur.fetchall()]
    if not metas:
        return None
    return {
        'durationMs': sum(m.get('durationMs') or 0 for m in metas),
        'tokensInput': max(m.get('tokensInput') or 0 for m in metas),
        'tokensOutput': sum(m.get('tokensOutput') or 0 for m in metas),
        'cost': round(sum(m.get('cost') or 0 for m in metas), 6),
    }


def get_batch_usage(batch_id: str) -> dict | None:
    """Batch-level usage: sum each child's own get_session_usage() across the
    whole batch. Unlike within one session, children are independent
    OpenCode contexts (separate files/prompts), so tokensInput sums here
    too — there's no shared growing context to dedupe via max().

    Returns None when no child has usable meta yet (batch still pending).
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            # F8：与 get_batch_detail 同口径——软删的子任务不进对外 usage。
            cur.execute("SELECT id FROM ai_chat_sessions "
                        "WHERE batch_id = %s AND deleted_at IS NULL", (batch_id,))
            child_ids = [r[0] for r in cur.fetchall()]
    per_child = [get_session_usage(cid) for cid in child_ids]
    usable = [u for u in per_child if u]
    if not usable:
        return None
    return {
        'durationMs': sum(u['durationMs'] for u in usable),
        'tokensInput': sum(u['tokensInput'] for u in usable),
        'tokensOutput': sum(u['tokensOutput'] for u in usable),
        'cost': round(sum(u['cost'] for u in usable), 6),
    }


def _text_from_content(content) -> str | None:
    """把 ai_chat_messages.content（typed parts 数组）里的 text 片段拼成纯文本。

    工具调用等非 text 片段一律丢弃 —— 对外只承诺「AI 的最终回复文本」，
    不暴露 OpenCode 的消息结构与 MCP 工具名。
    """
    if not content or not isinstance(content, list):
        return None
    texts = [p.get('text') for p in content
             if isinstance(p, dict) and p.get('type') == 'text' and p.get('text')]
    return '\n'.join(texts) if texts else None


MAX_CHILD_MESSAGES = 500


def get_child_messages(session_id: str,
                       limit: int = MAX_CHILD_MESSAGES) -> dict:
    """某个子任务的对话（只读）。

    按 `seq` 排序。有界：取最近 limit 条后再反转为升序，让调用方拿到自然阅读顺序。
    与 admin_get_child_messages 不同：不做 batch_id 校验（调用方已通过 get_batch_detail
    校验过归属）。
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT count(*) AS n FROM ai_chat_messages WHERE session_id = %s",
                        (session_id,))
            total = cur.fetchone()['n']
            cur.execute(
                "SELECT id, role, content, created_at FROM ai_chat_messages "
                " WHERE session_id = %s ORDER BY seq DESC LIMIT %s",
                (session_id, limit))
            rows = [dict(r) for r in cur.fetchall()]
    rows.reverse()
    return {'messages': rows, 'truncated': total > len(rows), 'total': total}


# ---------------------------------------------------------------------------
# 管理员作用域（跨用户）—— 只读
#
# 与上面那些函数的语义**相反**：这里刻意不按归属用户过滤。之所以另起函数而不是把
# 既有函数的 user_id 改成可空，是因为那样一次手滑（忘传参数）会从"报错"变成
# "返回全部用户的数据"——这类默认值是安全事故的常见来源。写路径不设这种函数：
# 重试走 admin_get_batch_owner 拿到归属用户后，复用既有的按归属过滤的写函数。
# ---------------------------------------------------------------------------

MAX_ADMIN_MESSAGES = 500

_ADMIN_SELECT = """
    SELECT b.*, u.username AS owner_username,
           CASE WHEN b.api_key_id IS NULL THEN 'ui' ELSE 'api' END AS source
      FROM ai_chat_batches b
      JOIN users u ON u.id = b.user_id
"""


def _admin_filters(status, owner_keyword, source, name_keyword):
    """把筛选条件编成 (where_sql, params)。空条件不产生任何谓词。"""
    where, params = [], []
    if status:
        where.append("b.status = %s")
        params.append(status)
    if owner_keyword:
        where.append("u.username ILIKE %s")
        params.append(f'%{owner_keyword}%')
    if source == 'ui':
        where.append("b.api_key_id IS NULL")
    elif source == 'api':
        where.append("b.api_key_id IS NOT NULL")
    if name_keyword:
        where.append("b.name ILIKE %s")
        params.append(f'%{name_keyword}%')
    return (' WHERE ' + ' AND '.join(where)) if where else '', params


def admin_list_batches(*, page: int, page_size: int,
                       status: str | None = None,
                       owner_keyword: str | None = None,
                       source: str | None = None,
                       name_keyword: str | None = None) -> dict:
    """跨全部用户列出批任务，附归属用户名与来源。"""
    where, params = _admin_filters(status, owner_keyword, source, name_keyword)
    offset = (page - 1) * page_size
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                _ADMIN_SELECT + where + " ORDER BY b.created_at DESC LIMIT %s OFFSET %s",
                (*params, page_size, offset))
            items = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT count(*) AS n FROM ai_chat_batches b "
                "JOIN users u ON u.id = b.user_id" + where, tuple(params))
            total = cur.fetchone()['n']
    return {'items': items, 'total': total, 'page': page, 'pageSize': page_size}


def admin_get_batch_detail(batch_id: str) -> dict | None:
    """跨用户取批任务详情 + 子任务清单。"""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_ADMIN_SELECT + " WHERE b.id = %s", (batch_id,))
            batch = cur.fetchone()
            if not batch:
                return None
            cur.execute(
                "SELECT id, status, batch_seq, batch_input_file, error_message, "
                "       last_message_preview "
                "  FROM ai_chat_sessions "
                "  WHERE batch_id = %s AND deleted_at IS NULL ORDER BY batch_seq",
                (batch_id,))
            sessions = [dict(r) for r in cur.fetchall()]
    return {'batch': dict(batch), 'sessions': sessions}


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
    from utils.batch_engine import _recompute_batch_status
    _recompute_batch_status(batch_id)
    return {'seq': row['batch_seq'], 'status': row['status'],
            'deletedAt': deleted_at.isoformat() if deleted_at else None}


def admin_get_batch_owner(batch_id: str) -> str | None:
    """该批任务的归属用户 id。写路径专用：拿到它之后复用按归属过滤的写函数，
    这样系统里永远不存在"不按归属过滤的写"。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM ai_chat_batches WHERE id = %s", (batch_id,))
            row = cur.fetchone()
    return row[0] if row else None


def admin_get_child_session(batch_id: str, session_id: str) -> dict | None:
    """跨用户取单个子任务的关键列。校验 session 属于该 batch。

    轻量单行查询：返回 {id, status, workspace_path, ownerUserId} 或 None。
    workspace_path 可能为 NULL（子会话归档/清理后）；调用方据此区分
    files 端点返空数组 vs preview/download/import 返 400 NO_WORKSPACE。
    ownerUserId 来自 JOIN ai_chat_batches，用于导入归属（uploaded_by=owner）。

    不复用 admin_get_batch_detail（拉整批 sessions 的 N+1 重调用）；本场景是单 session 操作。
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT s.id, s.status, s.workspace_path, b.user_id AS \"ownerUserId\" "
                "  FROM ai_chat_sessions s "
                "  JOIN ai_chat_batches b ON b.id = s.batch_id "
                " WHERE s.id = %s AND s.batch_id = %s",
                (session_id, batch_id),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def admin_get_child_messages(batch_id: str, session_id: str,
                             limit: int = MAX_ADMIN_MESSAGES) -> dict | None:
    """某个子任务的对话（只读）。session 不属于该 batch 时返回 None。

    按 `seq` 而非 `created_at` 排序：worker 批量持久化时同事务内 now() 是常量，
    多条消息的 created_at 相同，按它排序取不出确定顺序（`ai_chat_messages.seq`
    这一列正是为此新增的）。

    有界：取**最近** limit 条后再反转为升序，让调用方拿到的始终是自然阅读顺序。
    子会话持久化的是完整对话含工具调用，长 agent 运行可能数百条、体积可观。
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT 1 FROM ai_chat_sessions WHERE id = %s AND batch_id = %s",
                (session_id, batch_id))
            if not cur.fetchone():
                return None
            cur.execute("SELECT count(*) AS n FROM ai_chat_messages WHERE session_id = %s",
                        (session_id,))
            total = cur.fetchone()['n']
            cur.execute(
                "SELECT id, role, content, created_at, meta FROM ai_chat_messages "
                " WHERE session_id = %s ORDER BY seq DESC LIMIT %s",
                (session_id, limit))
            rows = [dict(r) for r in cur.fetchall()]
    rows.reverse()
    return {'messages': rows, 'truncated': total > len(rows), 'total': total}
