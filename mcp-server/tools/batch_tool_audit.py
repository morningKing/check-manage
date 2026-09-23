"""Tool: batch_tool_audit — 批任务级工具执行审计聚合(误报甄别的数据层)。

对指定批任务下的全部子会话,聚合指定工具(默认 bash)的执行记录:
- 命令、输出摘录、执行状态、错误标记(Traceback/error/failed/报错…,
  大小写不敏感的确定性预筛);
- 同消息内 AI 的文本陈述摘录(上下文对照用)。

本工具只做确定性事实聚合;"是否误报"的判断由分析方(trace-analyzer 技能/
模型)基于这些事实做出——账本事实在前,模型判断在后。
数据来源:ai_chat_messages.content 的 tool_use parts(批 worker 增量持久化),
与 analyze_trace 同源;跨会话聚合为本工具的增量价值。
"""
import json
import re

import mcp.types as types

from db import get_db
from context import ToolContext

NAME = "batch_tool_audit"

_MAX_TEXT = 600
_MAX_CLAIM = 400

_ERROR_RE = re.compile(
    r'traceback|exception|failed|失败|报错|出错|command not found|'
    r'permission denied|no such file|无法|不存在|error',
    re.IGNORECASE)

TOOL = types.Tool(
    name=NAME,
    description=(
        "批任务工具执行审计:聚合指定批任务下所有子会话的工具执行记录"
        "(命令、输出摘录、错误标记、同消息 AI 陈述),用于甄别"
        "「脚本是否真的执行成功」这类误报。只提供确定性事实,"
        "不代替人工/模型判断。参数:batch_id(必填),tool(默认 bash),"
        "pattern(输出正则预筛,可选)。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "batch_id": {"type": "string", "description": "批任务 ID"},
            "tool": {"type": "string", "description": "工具名过滤,默认 bash"},
            "pattern": {"type": "string",
                        "description": "可选正则,对命令+输出再做一层预筛"},
        },
        "required": ["batch_id"],
        "additionalProperties": False,
    },
)


class BatchToolAuditError(Exception):
    pass


def _clip(text, maxlen=_MAX_TEXT):
    return (text or '').strip()[:maxlen]


def audit_batch(batch_id: str, tool: str = 'bash',
                pattern: str | None = None) -> dict:
    """聚合批任务下全部子会话的指定工具执行记录。

    返回 {'batchId', 'tool', 'sessions': [{sessionId, executions: [
        {messageId, command, output, status, errorMarkers}]}],
        'summary': {'sessions', 'executions', 'errorMarked'}}。"""
    pat = re.compile(pattern, re.IGNORECASE) if pattern else None

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM ai_chat_sessions WHERE batch_id = %s "
                "ORDER BY batch_seq NULLS LAST",
                (batch_id,),
            )
            sids = [r[0] for r in cur.fetchall()]

    sessions = []
    total = error_marked = 0
    for sid in sids:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, role, content, created_at
                    FROM ai_chat_messages WHERE session_id = %s
                    ORDER BY COALESCE(seq, 0) ASC, created_at ASC
                    """,
                    (sid,),
                )
                rows = cur.fetchall()

        execs = []
        for mid, role, content, created in rows:
            for p in (content or []):
                if not isinstance(p, dict) or p.get('type') != 'tool_use':
                    continue
                if p.get('name') != tool:
                    continue
                state = p.get('state') or {}
                out_text = str(p.get('output') or '')
                cmd = _clip(str(p.get('input') or ''))
                entry = {
                    'messageId': mid,
                    'command': _clip(cmd),
                    'output': _clip(out_text),
                    'status': p.get('status') or state.get('status') or 'unknown',
                    'errorMarkers': sorted({t.group(0).lower()
                                            for t in _ERROR_RE.finditer(
                                                cmd + ' ' + out_text)}),
                    'occurredAt': str(created or ''),
                }
                execs.append(entry)

        if pattern:
            execs = [e for e in execs
                     if pat.search(e['command'] + ' ' + e['output'])]
        if execs:
            sessions.append({'sessionId': sid, 'executions': execs})
            total += len(execs)
            error_marked += sum(1 for e in execs
                                if 'error' in (e.get('errorMarkers') or []))

    return {'batchId': batch_id, 'tool': tool, 'sessions': sessions,
            'summary': {'sessions': len(sessions), 'executions': total,
                        'errorMarked': error_marked}}


def handle(input: dict, ctx: ToolContext) -> dict:
    """MCP 入口:校验参数后执行聚合;pattern 非法时转领域异常。"""
    inp = input or {}
    batch_id = (inp.get("batch_id") or "").strip()
    if not batch_id:
        raise BatchToolAuditError("batch_id is required")
    tool = (inp.get("tool") or 'bash').strip()
    pattern = (inp.get("pattern") or '').strip() or None
    try:
        return audit_batch(batch_id, tool=tool, pattern=pattern)
    except re.error as e:
        raise BatchToolAuditError(f"pattern 不是合法正则: {e}")
