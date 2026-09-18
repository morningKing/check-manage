"""Admin session list/detail/messages/files — unified cross-type query.

Replaces the limited `GET /ai/chat/admin/sessions` which only returns
non-batch sessions with no pagination or filters.

Session source types (computed in SQL):
  regular   — interactive chat (batch_id IS NULL, scan/kefu IS NULL)
  batch     — UI-created batch child (batch_id NOT NULL, api_key_id IS NULL)
  api_batch — API-created batch child (batch_id NOT NULL, api_key_id NOT NULL)
  scan      — scan-task child (scan_task_id IS NOT NULL)
  kefu      — customer-service session (kefu_instance_id IS NOT NULL)
"""
from psycopg2.extras import RealDictCursor

from db import get_db

VALID_STATUSES = {
    'active', 'closed', 'deleted', 'archived',
    'pending', 'running', 'completed', 'failed',
}
VALID_SOURCE_TYPES = {'regular', 'batch', 'api_batch', 'scan', 'kefu'}
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

_SOURCE_EXPR = """
    CASE
      WHEN s.kefu_instance_id IS NOT NULL THEN 'kefu'
      WHEN s.scan_task_id IS NOT NULL THEN 'scan'
      WHEN s.batch_id IS NOT NULL AND b.api_key_id IS NOT NULL THEN 'api_batch'
      WHEN s.batch_id IS NOT NULL THEN 'batch'
      ELSE 'regular'
    END"""

_FROM_CLAUSE = """
    FROM ai_chat_sessions s
    LEFT JOIN users u ON u.id = s.user_id
    LEFT JOIN ai_chat_batches b ON b.id = s.batch_id"""


def _build_where(status, source_type, owner, keyword, batch_id,
                 kind: str | None = None):
    """Build (where_sql, params) from optional filters.

    kind（execution-audit UX）：轨迹分析会话默认从管理列表隐藏；
      kind='trace_analysis' 只看分析会话，kind='all' 不过滤，缺省排除。"""
    where, params = [], []
    if kind == 'trace_analysis':
        where.append("COALESCE(s.kind, 'chat') = 'trace_analysis'")
    elif kind == 'all':
        pass
    elif kind == 'chat':
        where.append("COALESCE(s.kind, 'chat') = 'chat'")
    else:
        where.append("COALESCE(s.kind, 'chat') <> 'trace_analysis'")
    if status:
        where.append("s.status = %s")
        params.append(status)
    if source_type:
        where.append(f"{_SOURCE_EXPR} = %s")
        params.append(source_type)
    if owner:
        where.append("u.username ILIKE %s")
        params.append(f'%{owner}%')
    if keyword:
        # 关键词命中标题/输入文件/最近消息预览之外，还要命中完整历史消息
        # （批任务搜索 Spec §9 管理员一致性：不再仅依赖 last_message_preview，
        # 运行中/失败会话的历史消息同样可搜）。jsonb 只在命中其余过滤条件
        # 的行上展开，批量场景先靠 batch_id/owner 等条件收窄。
        where.append(
            "(s.title ILIKE %s OR s.batch_input_file ILIKE %s "
            "OR s.last_message_preview ILIKE %s "
            "OR EXISTS ( "
            "  SELECT 1 FROM ai_chat_messages m2 "
            "  CROSS JOIN jsonb_array_elements(m2.content) p "
            "  WHERE m2.session_id = s.id "
            "    AND p->>'type' = 'text' AND p->>'text' ILIKE %s))")
        kw = f'%{keyword}%'
        params.extend([kw, kw, kw, kw])
    if batch_id:
        where.append("s.batch_id = %s")
        params.append(batch_id)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    return where_sql, tuple(params)


def admin_list_sessions_v2(*, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE,
                           status: str | None = None,
                           source_type: str | None = None,
                           owner: str | None = None,
                           keyword: str | None = None,
                           batch_id: str | None = None,
                           kind: str | None = None) -> dict:
    """Unified paginated session list with optional filters.

    Returns {items: [dict], total: int, page: int, pageSize: int}.
    """
    page = max(1, page)
    page_size = min(max(1, page_size), MAX_PAGE_SIZE)
    where_sql, params = _build_where(status, source_type, owner, keyword,
                                     batch_id, kind)
    offset = (page - 1) * page_size

    select_cols = """
        s.id, s.user_id, s.title, s.status, s.created_at, s.last_active_at,
        s.batch_id, s.batch_seq, s.batch_input_file,
        s.error_message, s.last_message_preview,
        s.scan_task_id, s.opencode_session_id, s.kind,
        u.username,
        b.name AS batch_name"""

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT count(*) AS n {_FROM_CLAUSE}{where_sql}",
                params,
            )
            total = cur.fetchone()['n']
            cur.execute(
                f"SELECT {select_cols}, {_SOURCE_EXPR} AS source_type "
                f"{_FROM_CLAUSE}{where_sql} "
                "ORDER BY s.created_at DESC "
                "LIMIT %s OFFSET %s",
                (*params, page_size, offset),
            )
            items = [dict(r) for r in cur.fetchall()]
    return {'items': items, 'total': total, 'page': page, 'pageSize': page_size}


def admin_get_session_detail(session_id: str) -> dict | None:
    """Single session with all columns + computed source_type."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT s.*, u.username, b.name AS batch_name, "
                f"  b.api_key_id AS batch_api_key_id, "
                f"  {_SOURCE_EXPR} AS source_type "
                f"{_FROM_CLAUSE} "
                "WHERE s.id = %s",
                (session_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def admin_get_session_messages(session_id: str, limit: int = 500) -> dict:
    """Session messages (no batch_id check — admin context).

    Returns {messages: [dict], truncated: bool, total: int}.
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT count(*) AS n FROM ai_chat_messages WHERE session_id = %s",
                (session_id,),
            )
            total = cur.fetchone()['n']
            cur.execute(
                "SELECT id, role, content, created_at, meta "
                "FROM ai_chat_messages "
                "WHERE session_id = %s ORDER BY seq DESC LIMIT %s",
                (session_id, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
    rows.reverse()
    return {'messages': rows, 'truncated': total > len(rows), 'total': total}
