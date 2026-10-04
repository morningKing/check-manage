"""Tool: batch_children_search — 按条件筛选批任务下的子会话。

在指定批任务的（未删除）子任务范围内做条件筛选：
- status：执行状态白名单（多选，IN 匹配）；
- gate_status：门禁结论白名单（passed/failed/skipped/inconclusive，
  另支持 'unchecked' 表示尚未核对即 gate_status IS NULL）；
- has_error：是否带错误信息（error_message 非空/为空）；
- seq_from / seq_to：batch_seq 区间（闭区间）；
- keyword：对 文件名/会话标题/最后消息预览 做不区分大小写子串匹配
  （%/_ 通配符会被转义，按字面匹配）；
- limit：返回条数上限（默认 100，最大 500）。

返回：筛选命中总数（matched，对全量筛选集统计，不受 limit 截断）、
状态分布（statusCounts）、命中明细（按 batch_seq 升序，受 limit 截断）。
耗时口径：last_active_at - created_at（该子任务至今/至结束的生命周期）。

归属校验：批任务必须属于 MCP token 对应的用户——不能枚举他人批任务。
"""
from datetime import datetime, time, timedelta

import mcp.types as types

from db import get_db
from context import ToolContext
from tools.batch_children_status import _STATUS_ZH

NAME = "batch_children_search"

_STATUSES = sorted(_STATUS_ZH)
_GATE_STATUSES = ('passed', 'failed', 'skipped', 'inconclusive', 'unchecked')
_MAX_LIMIT = 500

TOOL = types.Tool(
    name=NAME,
    description=(
        "按条件筛选批任务下的子会话:在指定批任务的子任务范围内,按执行状态"
        "(status 多选)、门禁结论(gate_status 多选,含 unchecked=未核对)、"
        "是否报错(has_error)、序号区间(seq_from/seq_to)、创建时间窗"
        "(created_from/created_to,YYYY-MM-DD)、关键词"
        "(匹配文件名/标题/最后消息预览)组合筛选,返回命中总数、状态分布与"
        "命中明细(子任务 ID/序号/文件/状态/门禁/错误/预览摘要)。"
        "查「某天创建的子会话」传 created_from=created_to=那一天即可。"
        "参数:batch_id(必填),其余条件可选、可组合;limit 默认 100 上限 500,"
        "matched 计数不受 limit 截断。"
        "仅能查询自己(MCP token 归属用户)的批任务。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "batch_id": {"type": "string", "description": "批任务 ID"},
            "status": {
                "type": "array", "items": {"type": "string"},
                "description": f"执行状态多选，可选值：{'/'.join(_STATUSES)}",
            },
            "gate_status": {
                "type": "array", "items": {"type": "string"},
                "description": (
                    "门禁结论多选，可选值：passed/failed/skipped/"
                    "inconclusive/unchecked(未核对)"),
            },
            "has_error": {
                "type": "boolean",
                "description": "true=只看有错误的, false=只看无错误的",
            },
            "seq_from": {"type": "integer", "description": "序号下界(含)"},
            "seq_to": {"type": "integer", "description": "序号上界(含)"},
            "keyword": {
                "type": "string",
                "description": "子串关键词(不区分大小写),匹配文件名/标题/最后消息预览",
            },
            "created_from": {
                "type": "string",
                "description": "创建时间下界,YYYY-MM-DD(本地时区当日 0 点起)或 ISO 时间戳",
            },
            "created_to": {
                "type": "string",
                "description": "创建时间上界,YYYY-MM-DD(含当日全天)或 ISO 时间戳",
            },
            "limit": {"type": "integer",
                      "description": f"返回明细上限,默认 100,最大 {_MAX_LIMIT}"},
        },
        "required": ["batch_id"],
        "additionalProperties": False,
    },
)


class BatchChildrenSearchError(Exception):
    pass


def _esc_like(kw: str) -> str:
    """转义 ILIKE 通配符,关键词按字面匹配。"""
    return kw.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _build_filters(inp: dict):
    """校验并归一化筛选条件 → (SQL 片段列表, 参数列表, 归一化条件 dict)。
    非法取值直接抛领域异常并给出可选值清单。"""
    where, params, norm = [], [], {}

    statuses = inp.get('status') or []
    if statuses:
        bad = [s for s in statuses if s not in _STATUSES]
        if bad:
            raise BatchChildrenSearchError(
                f"非法 status: {bad}，可选值: {'/'.join(_STATUSES)}")
        where.append("s.status = ANY(%s)")
        params.append(list(statuses))
        norm['status'] = list(statuses)

    gates = inp.get('gate_status') or []
    if gates:
        bad = [g for g in gates if g not in _GATE_STATUSES]
        if bad:
            raise BatchChildrenSearchError(
                f"非法 gate_status: {bad}，可选值: {'/'.join(_GATE_STATUSES)}")
        if 'unchecked' in gates:
            rest = [g for g in gates if g != 'unchecked']
            if rest:
                where.append("(s.gate_status IS NULL OR s.gate_status = ANY(%s))")
                params.append(rest)
            else:
                where.append("s.gate_status IS NULL")
        else:
            where.append("s.gate_status = ANY(%s)")
            params.append(list(gates))
        norm['gate_status'] = list(gates)

    has_error = inp.get('has_error')
    if has_error is not None:
        where.append("s.error_message IS NOT NULL" if has_error
                     else "s.error_message IS NULL")
        norm['has_error'] = bool(has_error)

    seq_from, seq_to = inp.get('seq_from'), inp.get('seq_to')
    if seq_from is not None:
        where.append("s.batch_seq >= %s")
        params.append(int(seq_from))
        norm['seq_from'] = int(seq_from)
    if seq_to is not None:
        where.append("s.batch_seq <= %s")
        params.append(int(seq_to))
        norm['seq_to'] = int(seq_to)
    if (seq_from is not None and seq_to is not None
            and int(seq_from) > int(seq_to)):
        raise BatchChildrenSearchError("seq_from 不能大于 seq_to")

    # 创建时间窗：'YYYY-MM-DD'（本地时区）或完整 ISO 时间戳；
    # created_to 为纯日期时按「该日结束」处理（次日 0 点开区间）。
    created_from = (inp.get('created_from') or '').strip()
    created_to = (inp.get('created_to') or '').strip()
    try:
        if created_from:
            dt = datetime.fromisoformat(created_from)
            if len(created_from) == 10:
                dt = datetime.combine(dt.date(), time.min)
            where.append("s.created_at >= %s")
            params.append(dt.astimezone())
            norm['created_from'] = created_from
        if created_to:
            dt = datetime.fromisoformat(created_to)
            if len(created_to) == 10:
                dt = datetime.combine(dt.date() + timedelta(days=1), time.min)
                where.append("s.created_at < %s")
            else:
                where.append("s.created_at <= %s")
            params.append(dt.astimezone())
            norm['created_to'] = created_to
    except ValueError:
        raise BatchChildrenSearchError(
            "created_from/created_to 需为 YYYY-MM-DD 或 ISO 时间戳")

    keyword = (inp.get('keyword') or '').strip()
    if keyword:
        pat = f"%{_esc_like(keyword)}%"
        where.append("(s.batch_input_file ILIKE %s ESCAPE '\\' "
                     " OR s.title ILIKE %s ESCAPE '\\' "
                     " OR s.last_message_preview ILIKE %s ESCAPE '\\')")
        params.extend([pat, pat, pat])
        norm['keyword'] = keyword

    limit = inp.get('limit') or 100
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise BatchChildrenSearchError("limit 必须是整数")
    if not 1 <= limit <= _MAX_LIMIT:
        raise BatchChildrenSearchError(f"limit 需在 1~{_MAX_LIMIT} 之间")
    norm['limit'] = limit

    return where, params, norm


def search_children(batch_id: str, inp: dict) -> dict | None:
    """按条件筛选子会话。批任务不存在返回 None。"""
    where, params, norm = _build_filters(inp or {})
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, status FROM ai_chat_batches WHERE id = %s",
                (batch_id,))
            b = cur.fetchone()
            if not b:
                return None
            # 全量子任务数（不含筛选），给 matched 一个分母
            cur.execute(
                "SELECT count(*) FROM ai_chat_sessions s "
                "WHERE s.batch_id = %s AND s.deleted_at IS NULL", (batch_id,))
            total_children = cur.fetchone()[0]

            cond = " AND ".join(["s.batch_id = %s", "s.deleted_at IS NULL"]
                                + where)
            args = [batch_id] + params

            cur.execute(
                "SELECT s.status, count(*) FROM ai_chat_sessions s "
                f"WHERE {cond} GROUP BY s.status", args)
            status_counts = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute(
                "SELECT count(*) FROM ai_chat_sessions s "
                f"WHERE {cond} AND s.gate_status = 'failed'", args)
            gate_failed = cur.fetchone()[0]

            cur.execute(
                "SELECT s.id, s.title, s.batch_seq, s.batch_input_file, s.status, "
                "  s.gate_status, s.error_message, s.last_message_preview, "
                "  s.created_at, s.last_active_at, "
                "  (SELECT count(*) FROM action_expectations e "
                "   WHERE e.scope_id = s.id AND e.last_status = 'failed') AS gate_failed_n, "
                "  (SELECT count(*) FROM action_expectations e "
                "   WHERE e.scope_id = s.id AND e.last_status = 'passed') AS gate_passed_n "
                f"FROM ai_chat_sessions s WHERE {cond} "
                "ORDER BY s.batch_seq NULLS LAST LIMIT %s",
                args + [norm['limit']])
            children = []
            for r in cur.fetchall():
                (cid, title, seq, infile, st, gate_st, err, preview,
                 created, last_active, g_fail, g_pass) = r
                duration_ms = None
                if created and last_active:
                    duration_ms = int((last_active - created).total_seconds()
                                      * 1000)
                children.append({
                    'childId': cid,
                    'title': title,
                    'seq': seq,
                    'file': (infile or '').split('/')[-1] if infile else None,
                    'status': st,
                    'statusZh': _STATUS_ZH.get(st, st),
                    'gateStatus': gate_st,
                    'gatePassed': int(g_pass or 0),
                    'gateFailed': int(g_fail or 0),
                    'error': (err or None),
                    'preview': (preview or None),
                    'durationMs': duration_ms,
                    'createdAt': created.isoformat() if created else None,
                    'lastActiveAt': (last_active.isoformat()
                                     if last_active else None),
                })
    matched = sum(status_counts.values())
    return {
        'batchId': b[0], 'name': b[1], 'batchStatus': b[2],
        'filter': norm,
        'matched': matched,
        'totalChildren': int(total_children),
        'statusCounts': status_counts,
        'gateFailedCount': int(gate_failed),
        'children': children,
    }


def handle(input: dict, ctx: ToolContext) -> dict:
    """MCP 入口:校验参数 + 归属（批必须属于 MCP token 用户）后返回筛选结果。"""
    inp = dict(input or {})
    batch_id = (inp.get("batch_id") or "").strip()
    if not batch_id:
        raise BatchChildrenSearchError("batch_id is required")
    from db import get_db as _gdb
    with _gdb() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM ai_chat_batches WHERE id = %s",
                        (batch_id,))
            row = cur.fetchone()
    if not row or row[0] != ctx.user_id:
        # 不泄漏他人批任务存在性：归属不符与不存在同一报错
        raise BatchChildrenSearchError("批任务不存在")
    result = search_children(batch_id, inp)
    if result is None:
        raise BatchChildrenSearchError("批任务不存在")
    return result
