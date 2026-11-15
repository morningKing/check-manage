"""DB layer for ai_chat_batches and their child ai_chat_sessions rows.

Routes are thin; this module owns the SQL.
"""
import uuid
from psycopg2.extras import Json, RealDictCursor

from db import get_db

from .config import get_max_files_per_batch
from .queries import get_batch_detail
from .status import recompute_batch_status_tx


def create_batch(user_id: str, *, name: str, prompt: str,
                 template_id: str | None, files: list[dict],
                 scan_task_id: str | None = None,
                 agent: str | None = None,
                 model: str | None = None,
                 provision_repo: str | None = None,
                 provision_ref: str | None = None,
                 action_checks: list | None = None,
                 gate_policy_id: str | None = None,
                 gate_retry: bool | None = None,
                 api_key_id: str | None = None,
                 callback_url: str | None = None,
                 callback_secret: str | None = None,
                 subagent_reuse: list | None = None,
                 models: list | None = None) -> dict:
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
    # 门禁策略引用（spec §8.2）：存在且 enabled 才放行；路由层有同款预检，
    # 这里兜底（开放 API/定时扫描等直达 create_batch 的路径同样拦住）。
    # OC2 双栈（阶段四）：整批子会话钉同一栈（灰度开关，默认 'v1' 行为不变）
    from ai.runtime.registry import default_kind as _oc2_default_kind
    _oc_kind = _oc2_default_kind()
    if gate_policy_id:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT enabled FROM gate_policies WHERE id = %s",
                            (gate_policy_id,))
                row = cur.fetchone()
        if row is None:
            raise ValueError('引用的门禁策略不存在')
        if not row[0]:
            raise ValueError('引用的门禁策略已停用')
    # 0 文件合法:空批壳,后续 append 填充
    max_files = get_max_files_per_batch()
    if len(files) > max_files:
        raise ValueError(f"max {max_files} files per batch")

    batch_id = str(uuid.uuid4())
    # 多模型分配（2026-10-27）：批行存 models 全量列表，model 列保留首模型
    # （兼容既有读者）；≥2 个时子会话按 batch_seq 轮询写 model 列
    if not model and models:
        model = models[0]
    effective_models = models if (models and len(models) >= 2) else None
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO ai_chat_batches "
                "  (id, user_id, name, prompt, template_id, total, status, agent, model, "
                "   provision_repo, provision_ref, api_key_id, callback_url, callback_secret, "
                "   scan_task_id, action_checks, gate_policy_id, gate_retry, subagent_reuse, models) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (batch_id, user_id, name, prompt, template_id, len(files), agent, model,
                 provision_repo, provision_ref, api_key_id, callback_url, callback_secret,
                 scan_task_id,
                 Json(action_checks) if action_checks else None,
                 gate_policy_id,
                 gate_retry,
                 Json(subagent_reuse) if subagent_reuse else None,
                 Json(models) if models else None),
            )
            batch = dict(cur.fetchone())

            sessions = []
            for seq, f in enumerate(files):
                sid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, status, batch_id, batch_seq, batch_input_file, "
                    "   scan_task_id, source_record_id, model, oc_runtime) "
                    "VALUES (%s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                    (sid, user_id, batch_id, seq, f['path'],
                     scan_task_id, f.get('recordId'),
                     effective_models[seq % len(effective_models)] if effective_models else None,
                     _oc_kind),
                )
                sessions.append(dict(cur.fetchone()))
        conn.commit()
    return {'batch': batch, 'sessions': sessions}


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
            # 批没了，并发上限预算行一并清（scope 唯一索引按 batch_id 定位；
            # 残留虽无害——无 running 会话永不阻塞——但会越积越多）
            if deleted:
                cur.execute("DELETE FROM ai_execution_budgets WHERE scope_type='batch' AND scope_id=%s", (batch_id,))
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
            # 竞态矩阵（P0 §12）：cancel_batch 只对 pending 行置标志，worker
            # 未及 pre-claim 时会留下「pending 且 cancel_requested=true」——
            # resume 必须一并清除，否则该行被 claim 后立即取消（僵死组合）。
            # 放在 count 捕获之后：rowcount 属于上一条 UPDATE
            cur.execute(
                "UPDATE ai_chat_sessions SET cancel_requested = false, "
                "    pause_requested = false "
                "WHERE batch_id = %s AND status = 'pending' "
                "  AND (cancel_requested OR pause_requested)",
                (batch_id,),
            )
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
    # OC2 双栈：append 子会话沿用创建时的灰度开关（与 create_batch 同口径）
    from ai.runtime.registry import default_kind as _oc2_default_kind
    _oc_kind = _oc2_default_kind()
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
            # 多模型批追加的子会话同样轮询分配（序号接续全批 seq，分布不断档）
            cur.execute("SELECT models FROM ai_chat_batches WHERE id=%s", (batch_id,))
            _bmodels = cur.fetchone()['models']
            bmodels = _bmodels if (_bmodels and len(_bmodels) >= 2) else None
            sessions = []
            for i, f in enumerate(files):
                sid = str(uuid.uuid4())
                seq = start + i
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, status, batch_id, batch_seq, batch_input_file, model, oc_runtime) "
                    "VALUES (%s, %s, 'pending', %s, %s, %s, %s, %s) RETURNING *",
                    (sid, user_id, batch_id, seq, f['path'],
                     bmodels[seq % len(bmodels)] if bmodels else None,
                     _oc_kind),
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


def reset_failed_to_pending(user_id: str, batch_id: str, *,
                            api_key_id: str | None = None) -> int:
    """任务级「重试失败」：failed/needs_review 子任务全部重排为 pending，
    返回重排个数。同时回滚 batch.failed 计数并重算批次状态。

    2026-09-29 起与子会话「重新执行」同语义（全新一轮，不再原地半新半旧）：
    事务前先清空并重建每个目标子会话的工作区（上一轮残留文件会污染新一轮；
    uploads 由 reset_session_workspace 备份-恢复保留，staged 输入与 .opencode
    布置由 worker 派发时重新恢复），任一工作区重置失败即整体放弃、不动库
    （fail-closed，与 reexecute 路由同序）。事务内清上一轮的 消息/变更登记/
    子代理（含子代理消息）/工具调用账本/门禁期望/子代理复用锚点，并把
    opencode_session_id / retry_count / continue_prompt / gate 状态等执行态
    一并归零——新轮由派发路径全新建立。`api_key_id` non-None 额外把重置
    限定到该来源 key。
    """
    owner_scope = "SELECT id FROM ai_chat_batches WHERE user_id=%s"
    owner_params = [batch_id, user_id]
    if api_key_id is not None:
        owner_scope += " AND api_key_id = %s"
        owner_params.append(api_key_id)
    target_pred = ("batch_id=%s AND status IN ('failed','needs_review') "
                   f"AND batch_id IN ({owner_scope})")
    # 预读目标集只服务工作区重置；库内真集以事务内状态谓词为准，两者因并发
    # 有出入时多清的工作区无害（该子任务要么已被并发重排走全新派发路径）。
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, workspace_path FROM ai_chat_sessions "
                f"WHERE {target_pred}", tuple(owner_params))
            targets = cur.fetchall()
    # 工作区清空重建（fs 先于库，失败即放弃——与 reexecute 路由同序）
    from projects.workspace import reset_session_workspace
    for _sid, ws_path in targets:
        if ws_path:
            reset_session_workspace(ws_path)
    with get_db() as conn:
        with conn.cursor() as cur:
            # 上一轮关联行清零（语句顺序与 reexecute_child 一致，且必须排在
            # UPDATE 之前——状态翻转后谓词就匹配不到了）。
            cur.execute(
                f"DELETE FROM ai_chat_messages WHERE session_id IN "
                f"(SELECT id FROM ai_chat_sessions WHERE {target_pred})",
                tuple(owner_params))
            cur.execute(
                f"DELETE FROM ai_chat_session_files WHERE session_id IN "
                f"(SELECT id FROM ai_chat_sessions WHERE {target_pred})",
                tuple(owner_params))
            cur.execute(
                f"DELETE FROM ai_chat_subtask_messages WHERE subtask_id IN "
                f"(SELECT id FROM ai_chat_subtasks WHERE root_session_id IN "
                f"(SELECT id FROM ai_chat_sessions WHERE {target_pred}))",
                tuple(owner_params))
            cur.execute(
                f"DELETE FROM ai_chat_subtasks WHERE root_session_id IN "
                f"(SELECT id FROM ai_chat_sessions WHERE {target_pred})",
                tuple(owner_params))
            cur.execute(
                f"DELETE FROM agent_tool_calls WHERE root_session_id IN "
                f"(SELECT id FROM ai_chat_sessions WHERE {target_pred})",
                tuple(owner_params))
            cur.execute(
                f"DELETE FROM action_expectations WHERE scope_type='session' "
                f"AND scope_id IN "
                f"(SELECT id FROM ai_chat_sessions WHERE {target_pred})",
                tuple(owner_params))
            cur.execute(
                f"DELETE FROM ai_subagent_pins WHERE root_session_id IN "
                f"(SELECT id FROM ai_chat_sessions WHERE {target_pred})",
                tuple(owner_params))
            cur.execute(
                "UPDATE ai_chat_sessions "
                "SET status='pending', opencode_session_id=NULL, "
                "    last_message_preview=NULL, error_message=NULL, "
                "    cancel_requested=false, pause_requested=false, "
                "    gate_status=NULL, gate_error=NULL, retry_count=0, "
                "    continue_prompt=NULL, active_turn_id=NULL, "
                "    execution_generation = execution_generation + 1 "
                f"WHERE {target_pred}",
                tuple(owner_params),
            )
            count = cur.rowcount
            # 回滚 failed 计数（被重试的子任务此前计在 failed 里）+ F7：批次
            # 状态走统一重算（含 paused 规则），不再内联 CASE 硬算——有 paused
            # 子任务时旧写法会把本应 'paused' 的批次错写成 'pending'。
            if count:
                cur.execute("UPDATE ai_chat_batches SET failed = failed - %s "
                            "WHERE id = %s", (count, batch_id))
                recompute_batch_status_tx(cur, batch_id, conn=conn)
            conn.commit()
    return count


def force_stop_batch(batch_id: str, *, operator: str,
                     api_key_id: str | None = None) -> dict:
    """P1 §6.4 `force_stop`（缺口补齐 4）：drain 超时后的管理员强制通道。

    对全部非终态子会话：fencing_token += 1（使旧执行体的写回全部失效）、
    尽力 abort 其 OpenCode 回合、pending/running → cancelled（计入 failed）、
    paused → cancelled；attempt 收口为 stopped；批次置 failed。
    与 cancel_batch 的区别：不等 worker 协作收口，立即落终态。"""
    import logging as _logging
    _log = _logging.getLogger(__name__)
    stopped = {'cancelled': 0, 'ocAborted': 0}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_chat_sessions "
                "   SET fencing_token = fencing_token + 1, "
                "       status = 'cancelled', cancel_requested = false, "
                "       pause_requested = false, active_turn_id = NULL, "
                "       error_message = '已被管理员强制停止' "
                " WHERE batch_id = %s AND status IN ('pending', 'running', 'paused') "
                " RETURNING id, opencode_session_id, status",
                (batch_id,))
            rows = cur.fetchall()
            stopped['cancelled'] = len(rows)
            cur.execute(
                "UPDATE ai_chat_batches SET failed = failed + %s "
                "WHERE id = %s", (len(rows), batch_id))
            # attempt 收口（P1：终态子任务不留 running attempt）
            for r in rows:
                cur.execute(
                    "UPDATE ai_execution_attempts a SET status = 'stopped', "
                    "  finished_at = NOW() FROM ai_chat_sessions s "
                    " WHERE a.session_id = s.id AND s.id = %s "
                    "   AND a.status IN ('running', 'recovering')", (r[0],))
        conn.commit()
    # OpenCode abort（best-effort，会话可能已不存在）
    from ai.opencode.opencode_client import OpenCodeClient as _OCClient
    from config import OPENCODE_BASE_URL as _OC_URL
    client = _OCClient(_OC_URL)
    for r in rows:
        oc = r[1]
        if oc:
            try:
                client.abort_session(oc)
                stopped['ocAborted'] += 1
            except Exception:
                pass
    from ai.batch.execution_audit import finish_latest_running
    for r in rows:
        try:
            finish_latest_running(r[0], 'stopped', error_code='FORCE_STOPPED')
        except Exception:
            pass
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_batches SET status='failed', "
                    "  completed_at=COALESCE(completed_at, NOW()) "
                    "WHERE id=%s", (batch_id,))
        conn.commit()
    from common.operation_log import log_operation
    log_operation('update', 'ai_chat_batch', batch_id, batch_id,
                  f'管理员强制停止（{operator}）：取消 {stopped["cancelled"]} 个子任务')
    _log.warning('force_stop batch=%s by=%s cancelled=%s', batch_id, operator,
                 stopped['cancelled'])
    return {'cancelled': stopped['cancelled'], **stopped}



__all__ = [
    'RESUME_CONTINUE_PROMPT',
    '_recompute_batch_status_for',
    'append_to_batch',
    'cancel_batch',
    'create_batch',
    'delete_batch',
    'force_stop_batch',
    'get_batch_detail',
    'get_max_files_per_batch',
    'get_db',
    'Json',
    'pause_batch',
    'RealDictCursor',
    'recompute_batch_status_tx',
    'reset_failed_to_pending',
    'resume_batch',
]
