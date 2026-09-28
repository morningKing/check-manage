"""Tool: batch_children_status — 批任务子任务状态/结果汇总。

对指定批任务的全部（未删除）子任务，汇总执行状态与结果：
- 子任务 ID、文件名、状态（pending/running/paused/completed/failed/cancelled）、
  门禁结论（gate_status + 通过/失败计数）、耗时、错误信息；
- 最后一条 assistant 消息摘要（每个子任务"执行到了什么结果"的一眼可读答案）；
- 批级聚合：total/done/failed/paused/cancelled、批次状态。

归属校验：批任务必须属于 MCP token 对应的用户——不能枚举他人批任务。
"""
import mcp.types as types

from db import get_db
from context import ToolContext

NAME = "batch_children_status"

TOOL = types.Tool(
    name=NAME,
    description=(
        "批任务子任务状态汇总:列出指定批任务下全部子任务的状态、门禁结论、"
        "耗时、错误信息与最后一条 AI 回复摘要(每个子任务执行结果的一眼可读总结),"
        "并附批级聚合(完成/失败/暂停计数)。参数:batch_id(必填)。"
        "仅能查询自己(MCP token 归属用户)的批任务。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "batch_id": {"type": "string", "description": "批任务 ID"},
        },
        "required": ["batch_id"],
        "additionalProperties": False,
    },
)

_STATUS_ZH = {
    'pending': '待运行', 'running': '运行中', 'paused': '已暂停',
    'completed': '已完成', 'partial': '部分完成', 'failed': '失败',
    'cancelled': '已取消', 'needs_review': '需人工复核',
}


class BatchChildrenStatusError(Exception):
    pass


def children_overview(batch_id: str) -> dict | None:
    """查询批任务 + 全部子任务的状态/结果汇总。批任务不存在(或不属于
    指定用户)返回 None。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, status, total, done, failed "
                "FROM ai_chat_batches WHERE id = %s", (batch_id,))
            b = cur.fetchone()
            if not b:
                return None
            batch = dict(zip(('id', 'name', 'status', 'total', 'done',
                              'failed'), b))
            cur.execute(
                "SELECT s.id, s.batch_seq, s.batch_input_file, s.status, "
                "  s.gate_status, s.error_message, s.last_message_preview, "
                "  s.last_active_at AS started_at, s.last_active_at AS finished_at, s.last_active_at, "
                "  (SELECT count(*) FROM action_expectations e "
                "   WHERE e.scope_id = s.id AND e.last_status = 'failed') AS gate_failed, "
                "  (SELECT count(*) FROM action_expectations e "
                "   WHERE e.scope_id = s.id AND e.last_status = 'passed') AS gate_passed, "
                "  (SELECT m.content FROM ai_chat_messages m "
                "   WHERE m.session_id = s.id AND m.role = 'assistant' "
                "   ORDER BY m.seq DESC LIMIT 1) AS last_reply "
                "FROM ai_chat_sessions s "
                "WHERE s.batch_id = %s AND s.deleted_at IS NULL "
                "ORDER BY s.batch_seq NULLS LAST", (batch_id,))
            children = []
            for r in cur.fetchall():
                (cid, seq, infile, st, gate_st, err, preview,
                 started, finished, last_active, g_fail, g_pass,
                 last_reply_json) = r
                # 最后一条 assistant 回复的首行文本 = 该子任务的结果摘要
                summary = None
                try:
                    import json as _json
                    parts = (last_reply_json if isinstance(last_reply_json, list)
                             else _json.loads(last_reply_json or '[]'))
                    texts = [p.get('text', '') for p in (parts or [])
                             if isinstance(p, dict) and p.get('type') == 'text']
                    if texts:
                        summary = '\n'.join(texts).strip()[:300] or None
                except Exception:
                    summary = None
                duration_ms = None
                if started and finished:
                    try:
                        duration_ms = int((finished - started).total_seconds() * 1000)
                    except Exception:
                        pass
                children.append({
                    'childId': cid,
                    'seq': seq,
                    'file': (infile or '').split('/')[-1] if infile else None,
                    'status': st,
                    'statusZh': _STATUS_ZH.get(st, st),
                    'gateStatus': gate_st,
                    'gatePassed': int(g_pass or 0),
                    'gateFailed': int(g_fail or 0),
                    'error': (err or None),
                    'resultSummary': summary,
                    'durationMs': duration_ms,
                    'lastActiveAt': (last_active.isoformat()
                                     if last_active else None),
                })
    return {
        'batchId': batch['id'],
        'name': batch['name'],
        'status': batch['status'],
        'aggregate': {
            'total': batch['total'], 'done': batch['done'],
            'failed': batch['failed'],
        },
        'children': children,
    }


def handle(input: dict, ctx: ToolContext) -> dict:
    """MCP 入口:校验参数 + 归属（批必须属于 MCP token 用户）后返回汇总。"""
    inp = input or {}
    batch_id = (inp.get("batch_id") or "").strip()
    if not batch_id:
        raise BatchChildrenStatusError("batch_id is required")
    from db import get_db as _gdb
    with _gdb() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM ai_chat_batches WHERE id = %s",
                        (batch_id,))
            row = cur.fetchone()
    if not row or row[0] != ctx.user_id:
        # 不泄漏他人批任务存在性：归属不符与不存在同一报错
        raise BatchChildrenStatusError("批任务不存在")
    result = children_overview(batch_id)
    if result is None:
        raise BatchChildrenStatusError("批任务不存在")
    return result
