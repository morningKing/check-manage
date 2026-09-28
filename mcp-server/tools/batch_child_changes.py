"""Tool: batch_child_changes — 已结束子任务的变更文件（新增/修改）查询。

对批任务下指定子任务（须已终态），列出其工作区扫描出的新增/修改文件：
- path、status（added/modified/deleted）、data_file_id（已导入系统的文件 id）；
- 仅返回该子任务自己的文件记录——不能查询同批其他子任务或他人会话。
归属校验：会话必须属于批任务，且批任务属于 MCP token 对应的用户。
"""
import mcp.types as types

from db import get_db
from context import ToolContext

NAME = "batch_child_changes"

TOOL = types.Tool(
    name=NAME,
    description=(
        "批任务子任务变更文件:列出指定已结束子任务工作区扫描出的"
        "新增/修改文件(path、added/modified 状态、已导入的 data_file_id)。"
        "参数:batch_id(必填)、session_id(子任务 ID,必填)。"
        "仅能查询自己(MCP token 归属用户)批任务下的子任务。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "batch_id": {"type": "string", "description": "批任务 ID"},
            "session_id": {"type": "string", "description": "子任务会话 ID"},
        },
        "required": ["batch_id", "session_id"],
        "additionalProperties": False,
    },
)


class BatchChildChangesError(Exception):
    pass


def child_changes(batch_id: str, session_id: str) -> dict | None:
    """返回子任务的变更文件列表。校验三重归属：会话属于该批、批存在、
    （调用方再核批归属用户）。返回 None = 会话/批不匹配。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.status, s.batch_seq, s.batch_input_file "
                "FROM ai_chat_sessions s WHERE s.id = %s AND s.batch_id = %s",
                (session_id, batch_id))
            sess = cur.fetchone()
            if not sess:
                return None
            child_status, batch_seq, infile = sess
            cur.execute(
                "SELECT path, status, data_file_id, first_seen_at, last_seen_at "
                "FROM ai_chat_session_files WHERE session_id = %s "
                "ORDER BY last_seen_at DESC", (session_id,))
            cols = ('path', 'status', 'dataFileId', 'firstSeenAt', 'lastSeenAt')
            files = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                for k in ('firstSeenAt', 'lastSeenAt'):
                    if d.get(k) is not None:
                        d[k] = d[k].isoformat()
                files.append(d)
    return {
        'batchId': batch_id,
        'sessionId': session_id,
        'childStatus': child_status,
        'batchSeq': batch_seq,
        'inputFile': infile,
        'files': files,
    }


def handle(input: dict, ctx: ToolContext) -> dict:
    """MCP 入口:校验参数 + 批归属（批必须属于 MCP token 用户）后返回文件列表。"""
    inp = input or {}
    batch_id = (inp.get("batch_id") or "").strip()
    session_id = (inp.get("session_id") or "").strip()
    if not batch_id or not session_id:
        raise BatchChildChangesError("batch_id and session_id are required")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM ai_chat_batches WHERE id = %s", (batch_id,))
            row = cur.fetchone()
    if not row:
        raise BatchChildChangesError("批任务不存在")
    if row[0] != ctx.user_id:
        raise BatchChildChangesError("批任务不存在")  # 不泄漏他人批任务存在性
    result = child_changes(batch_id, session_id)
    if result is None:
        raise BatchChildChangesError("该批任务下不存在此子任务")
    return result
