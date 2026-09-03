"""Tool: query_sessions — 查询历史 AI 会话，支持多维过滤。

只读查询，供 trace-analyzer Skill 查找同类成功/失败案例做对比分析。
"""

import json
from datetime import datetime

import mcp.types as types
from db import get_db
from context import ToolContext

NAME = "query_sessions"

TOOL = types.Tool(
    name=NAME,
    description=(
        "查询历史 AI 会话，支持按来源类型、状态、Agent、关键词等过滤。"
        "用于查找同类任务的成功/失败案例做对比分析。"
        "参数: source_type(batch|scan|interactive|open_api), status(completed|failed|active),"
        " agent, scan_task_id, batch_id, keyword, created_after, created_before, limit(默认10)。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "source_type": {
                "type": "string",
                "enum": ["interactive", "batch", "scan", "row_action", "open_api"],
                "description": "按来源类型过滤",
            },
            "status": {
                "type": "string",
                "enum": ["completed", "failed", "active", "cancelled"],
                "description": "按状态过滤",
            },
            "agent": {
                "type": "string",
                "description": "按 Agent 名称过滤",
            },
            "scan_task_id": {
                "type": "string",
                "description": "按扫描任务 ID 过滤",
            },
            "batch_id": {
                "type": "string",
                "description": "按批任务 ID 过滤",
            },
            "keyword": {
                "type": "string",
                "description": "标题或错误消息关键词（模糊匹配）",
            },
            "created_after": {
                "type": "string",
                "description": "起始时间（ISO 8601，如 2026-09-01）",
            },
            "created_before": {
                "type": "string",
                "description": "结束时间（ISO 8601）",
            },
            "limit": {
                "type": "integer",
                "description": "返回数量（默认 10，最大 50）",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
)


class QuerySessionsError(Exception):
    pass


def _trunc(text, maxlen: int):
    if not text:
        return text
    s = str(text)
    return s[:maxlen] + "..." if len(s) > maxlen else s


def _json_serial(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def handle(input: dict, ctx: ToolContext) -> dict:
    """查询历史会话。"""
    inp = input or {}
    conditions = []
    params = []

    # ── 来源类型过滤 ───────────────────────────────────────────────────
    st = inp.get("source_type")
    if st == "scan":
        conditions.append("s.scan_task_id IS NOT NULL")
    elif st == "batch":
        conditions.append("s.batch_id IS NOT NULL AND s.scan_task_id IS NULL")
    elif st == "open_api":
        conditions.append("s.api_key_id IS NOT NULL")
    elif st == "interactive":
        conditions.append("s.batch_id IS NULL AND s.api_key_id IS NULL")

    # ── 状态过滤 ──────────────────────────────────────────────────────
    status = inp.get("status")
    if status:
        conditions.append("s.status = %s")
        params.append(status)

    # ── Agent 过滤 ─────────────────────────────────────────────────────
    agent = inp.get("agent")
    if agent:
        conditions.append("(s.agent = %s OR b.agent = %s)")
        params.extend([agent, agent])

    # ── 扫描任务过滤 ──────────────────────────────────────────────────
    scan_tid = inp.get("scan_task_id")
    if scan_tid:
        conditions.append("s.scan_task_id = %s")
        params.append(scan_tid)

    # ── 批任务过滤 ─────────────────────────────────────────────────────
    batch_id = inp.get("batch_id")
    if batch_id:
        conditions.append("s.batch_id = %s")
        params.append(batch_id)

    # ── 关键词搜索 ─────────────────────────────────────────────────────
    keyword = inp.get("keyword")
    if keyword:
        conditions.append(
            "(s.title ILIKE %s OR s.error_message ILIKE %s OR s.last_message_preview ILIKE %s)"
        )
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw])

    # ── 时间范围 ──────────────────────────────────────────────────────
    created_after = inp.get("created_after")
    if created_after:
        conditions.append("s.created_at >= %s")
        params.append(created_after)
    created_before = inp.get("created_before")
    if created_before:
        conditions.append("s.created_at <= %s")
        params.append(created_before)

    # ── 只返回有消息的终态会话 ────────────────────────────────────────
    if not status:
        conditions.append("s.status IN ('completed', 'failed', 'cancelled')")

    where = " AND ".join(conditions) if conditions else "1=1"
    limit = min(max(int(inp.get("limit", 10)), 1), 50)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT s.id, s.status, s.agent, s.error_message,
                   s.created_at, s.last_active_at, s.last_message_preview,
                   s.scan_task_id, s.batch_id, s.api_key_id,
                   b.agent AS batch_agent, b.name AS batch_name,
                   COUNT(m.id) AS msg_count,
                   COALESCE(SUM((m.meta->>'durationMs')::int), 0)     AS total_duration,
                   COALESCE(SUM(COALESCE((m.meta->>'tokensInput')::int, 0)
                               + COALESCE((m.meta->>'tokensOutput')::int, 0)), 0) AS total_tokens,
                   COALESCE(SUM((m.meta->>'cost')::numeric), 0)       AS total_cost
            FROM ai_chat_sessions s
            LEFT JOIN ai_chat_batches b ON b.id = s.batch_id
            LEFT JOIN ai_chat_messages m ON m.session_id = s.id AND m.role = 'assistant'
            WHERE {where}
            GROUP BY s.id, s.status, s.agent, s.error_message,
                     s.created_at, s.last_active_at, s.last_message_preview,
                     s.scan_task_id, s.batch_id, s.api_key_id,
                     b.agent, b.name
            ORDER BY s.created_at DESC
            LIMIT %s
            """,
            params + [limit],
        )

        sessions = []
        for row in cur.fetchall():
            # 推断来源类型
            if row[7]:   # scan_task_id
                source_type = "scan"
            elif row[8]: # batch_id
                source_type = "batch"
            elif row[9]: # api_key_id
                source_type = "open_api"
            else:
                source_type = "interactive"

            sessions.append({
                "id": row[0],
                "status": row[1],
                "agent": row[2] or row[10] or "",  # session.agent || batch.agent
                "error_message": _trunc(row[3], 200),
                "source_type": source_type,
                "created_at": row[4].isoformat() if row[4] else None,
                "last_active_at": row[5].isoformat() if row[5] else None,
                "last_message_preview": _trunc(row[6], 100),
                "duration_ms": row[13] or 0,
                "tokens_total": row[14] or 0,
                "cost": float(row[15] or 0),
                "message_count": row[12] or 0,
            })

    return json.dumps(
        {"sessions": sessions, "total": len(sessions)},
        ensure_ascii=False,
        default=_json_serial,
    )
