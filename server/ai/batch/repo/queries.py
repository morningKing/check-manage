from psycopg2.extras import RealDictCursor

from db import get_db


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
    # 批并发上限（2026-10-27）：随列表下发——编辑对话框从列表行预填，缺了
    # 会把已设上限回显成空、无辜保存即清除
    mc_sub = ("(SELECT b.max_concurrency FROM ai_execution_budgets b "
              " WHERE b.scope_type = 'batch' AND b.scope_id = ai_chat_batches.id"
              "   AND b.enabled)")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT *, {cancelled_sub} AS cancelled, {paused_sub} AS paused, "
                f"{mc_sub} AS max_concurrency "
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
                "   WHERE s.batch_id = ai_chat_batches.id AND s.status='paused') AS paused, "
                "  (SELECT b.max_concurrency FROM ai_execution_budgets b "
                "   WHERE b.scope_type='batch' AND b.scope_id = ai_chat_batches.id "
                "     AND b.enabled) AS max_concurrency "
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
                "       opencode_session_id, error_message, last_message_preview, model, "
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
    ai.sessions.ai_message_meta.meta_from_info/public_meta, one row per OpenCode
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
                "       last_message_preview, gate_status, gate_error "
                "  FROM ai_chat_sessions "
                "  WHERE batch_id = %s AND deleted_at IS NULL ORDER BY batch_seq",
                (batch_id,))
            sessions = [dict(r) for r in cur.fetchall()]
    return {'batch': dict(batch), 'sessions': sessions}


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
                "SELECT s.id, s.status, s.workspace_path, s.opencode_session_id, "
                "       b.user_id AS \"ownerUserId\" "
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


def get_batch_progress_ext(batch_id: str) -> dict:
    """P1 §8.3 对外状态扩展字段：generation / queueWaitMs / runningMs /
    lastProgressAt。全部 best-effort——任一失败返回空 dict，不进对外主契约。"""
    import datetime as _dt
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT max(execution_generation), max(last_active_at) "
                    "FROM ai_chat_sessions WHERE batch_id = %s", (batch_id,))
                gen, last_progress = cur.fetchone()
                cur.execute(
                    "SELECT b.created_at, "
                    "       min(s.created_at) FILTER (WHERE s.status IN "
                    "         ('running','completed','failed','cancelled','partial')) "
                    "FROM ai_chat_batches b LEFT JOIN ai_chat_sessions s "
                    "  ON s.batch_id = b.id "
                    "WHERE b.id = %s GROUP BY b.created_at", (batch_id,))
                created_at, first_started = cur.fetchone()
                cur.execute("SELECT completed_at FROM ai_chat_batches "
                            "WHERE id = %s", (batch_id,))
                completed_at = (cur.fetchone() or [None])[0]
                out = {'generation': gen}
                # P1-A1：批次级事件游标（事件流增量拉取的起点）与 paused
                # （任一子会话处于 paused 即视为批次可恢复暂停）。
                cur.execute("SELECT COALESCE(MAX(event_seq), 0) "
                            "FROM ai_batch_events WHERE batch_id = %s", (batch_id,))
                out['eventCursor'] = cur.fetchone()[0]
                cur.execute("SELECT EXISTS(SELECT 1 FROM ai_chat_sessions "
                            "WHERE batch_id = %s AND status = 'paused')", (batch_id,))
                out['paused'] = cur.fetchone()[0]
        if created_at and first_started:
            out['queueWaitMs'] = int(
                (first_started - created_at).total_seconds() * 1000)
        if first_started:
            end_at = completed_at or _dt.datetime.now(_dt.timezone.utc)
            if completed_at is None and first_started.tzinfo is None:
                end_at = _dt.datetime.now()
            out['runningMs'] = int(abs(
                (end_at - first_started).total_seconds() * 1000))
        if last_progress is not None:
            out['lastProgressAt'] = (last_progress.isoformat()
                                     if hasattr(last_progress, 'isoformat')
                                     else str(last_progress))
        return out
    except Exception:
        return {}


def get_children_progress(batch_id: str) -> list[dict]:
    """P1-A1：per-child 结构化状态（attempt/retryable/error/lastProgressAt）。
    best-effort，失败返回空列表。"""
    try:
        from psycopg2.extras import RealDictCursor
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, batch_seq, batch_input_file, status, "
                    "execution_generation, retry_count, error_message, "
                    "gate_status, gate_error, last_active_at "
                    "FROM ai_chat_sessions WHERE batch_id = %s "
                    "AND deleted_at IS NULL ORDER BY batch_seq", (batch_id,))
                rows = [dict(r) for r in cur.fetchall()]
        out = []
        for r in rows:
            terminal = r['status'] in ('completed', 'failed', 'cancelled', 'needs_review')
            out.append({
                'childId': r['id'],
                'seq': r['batch_seq'],
                'name': (r['batch_input_file'] or '').replace('\\', '/').rsplit('/', 1)[-1] if r['batch_input_file'] else None,
                'status': r['status'],
                'attempt': (r['execution_generation'] or 0) + 1,
                'retryCount': r['retry_count'] or 0,
                'retryable': not terminal,
                'lastProgressAt': r['last_active_at'].isoformat() if r.get('last_active_at') else None,
                # 评审跟进（2026-11-01）：核对不过的子会话以 completed 收口、
                # error_message 为 NULL——children 增量携带 gateStatus/gateError
                # （命名对齐 child.status 事件流），恢复门禁失败外部可见性。
                'gateStatus': r.get('gate_status'),
                'gateError': r.get('gate_error'),
                'error': {
                    'code': 'GATE_INCONCLUSIVE' if r.get('gate_status') == 'inconclusive' else None,
                    'message': r.get('error_message'),
                    'retryable': r['status'] in ('failed', 'needs_review'),
                },
            })
        return out
    except Exception:
        return []



__all__ = [
    'MAX_ADMIN_MESSAGES',
    'MAX_CHILD_MESSAGES',
    '_ADMIN_SELECT',
    '_admin_filters',
    '_text_from_content',
    'admin_get_batch_detail',
    'admin_get_batch_owner',
    'admin_get_child_messages',
    'admin_get_child_session',
    'admin_list_batches',
    'get_batch_detail',
    'get_batch_progress_ext',
    'get_batch_results',
    'get_batch_usage',
    'get_child_messages',
    'get_children_progress',
    'get_db',
    'get_session_usage',
    'list_batches',
    'RealDictCursor',
]
