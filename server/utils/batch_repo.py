"""DB layer for ai_chat_batches and their child ai_chat_sessions rows.

Routes are thin; this module owns the SQL.
"""
import uuid
from psycopg2.extras import RealDictCursor

from db import get_db


MAX_FILES_PER_BATCH = 50


def create_batch(user_id: str, *, name: str, prompt: str,
                 template_id: str | None, files: list[dict],
                 scan_task_id: str | None = None,
                 agent: str | None = None,
                 model: str | None = None,
                 provision_repo: str | None = None,
                 provision_ref: str | None = None,
                 api_key_id: str | None = None) -> dict:
    """Atomically insert a batch + N child sessions.

    `files` is a list of {name, path} dicts where `path` is workspace-relative
    (under batch-staging/...). Each entry may also carry an optional `recordId`
    key: the source record id, stamped into `source_record_id` for scan tasks.
    `scan_task_id` is an optional param linking the child sessions to a scan task.
    `agent` is an optional OpenCode agent name to use for this batch.
    `model` is an optional "<providerID>/<modelID>" to run this batch with;
    empty falls back to the global OPENCODE_MODEL / the agent's default.
    `api_key_id` stamps the source API key (open API callers) that created this
    batch; None for UI/scan-task created batches.
    Returns {batch, sessions}.
    """
    if not files:
        raise ValueError("at least one file required")
    if len(files) > MAX_FILES_PER_BATCH:
        raise ValueError(f"max {MAX_FILES_PER_BATCH} files per batch")

    batch_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO ai_chat_batches "
                "  (id, user_id, name, prompt, template_id, total, status, agent, model, "
                "   provision_repo, provision_ref, api_key_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s) RETURNING *",
                (batch_id, user_id, name, prompt, template_id, len(files), agent, model,
                 provision_repo, provision_ref, api_key_id),
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
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT * FROM ai_chat_batches {scope} "
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
            sql = "SELECT * FROM ai_chat_batches WHERE id=%s AND user_id=%s"
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
                "       opencode_session_id, error_message, last_message_preview "
                "FROM ai_chat_sessions WHERE batch_id=%s ORDER BY batch_seq",
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
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
            if not row:
                return None
            if row['total'] + len(files) > MAX_FILES_PER_BATCH:
                raise ValueError(f"max {MAX_FILES_PER_BATCH} files per batch")
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
    circular imports (batch_engine imports batch_repo)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT done, failed, total FROM ai_chat_batches WHERE id=%s", (batch_id,))
            row = cur.fetchone()
            if not row:
                return
            done, failed, total = row
            terminal = done + failed
            status = ('pending' if terminal == 0 else
                      'running' if terminal < total else
                      'failed' if failed == total else
                      'completed' if done == total else 'partial')
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
            if status not in ('completed', 'failed'):
                raise ValueError('only completed/failed children can be re-executed')
            cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (session_id,))
            cur.execute(
                "UPDATE ai_chat_sessions SET status='pending', opencode_session_id=NULL, "
                "  last_message_preview=NULL, error_message=NULL WHERE id = %s",
                (session_id,),
            )
            if status == 'completed':
                cur.execute("UPDATE ai_chat_batches SET done = done - 1 WHERE id = %s", (batch_id,))
            else:
                cur.execute("UPDATE ai_chat_batches SET failed = failed - 1 WHERE id = %s", (batch_id,))
        conn.commit()
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id)


def update_batch_config(user_id: str, batch_id: str, *,
                        agent: str | None, model: str | None,
                        provision_repo: str | None = None,
                        provision_ref: str | None = None) -> dict | None:
    """Update a batch's agent/model/provision repo (owner-only). NULL clears to
    the default. Returns updated detail, or None if not found / not owned. Takes
    effect on the next run the worker claims (retry / reexecute / pending), since
    the worker reads these fresh per run via _fetch_batch_context."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_chat_batches SET agent = %s, model = %s, "
                "  provision_repo = %s, provision_ref = %s "
                "WHERE id = %s AND user_id = %s",
                (agent, model, provision_repo, provision_ref, batch_id, user_id),
            )
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
                "SET status='pending', error_message=NULL "
                "WHERE batch_id=%s AND status='failed' "
                f"  AND batch_id IN ({owner_scope})",
                tuple(owner_params),
            )
            count = cur.rowcount
            if count:
                cur.execute(
                    "UPDATE ai_chat_batches SET failed = failed - %s, "
                    "  status = CASE WHEN done = total THEN 'completed' "
                    "                ELSE 'pending' END "
                    "WHERE id = %s",
                    (count, batch_id),
                )
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
        SELECT s.batch_input_file, s.status, s.error_message,
               (SELECT m.content FROM ai_chat_messages m
                 WHERE m.session_id = s.id AND m.role = 'assistant'
                 ORDER BY m.seq DESC LIMIT 1) AS content
          FROM ai_chat_sessions s
         WHERE s.batch_id = %s
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
        })
    return results


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
                "  FROM ai_chat_sessions WHERE batch_id = %s ORDER BY batch_seq",
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
