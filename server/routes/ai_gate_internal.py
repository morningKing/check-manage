"""P3-C3：PreToolUse 门禁拦截——OpenCode 插件在工具调用前校验（NOT for browsers）。

OpenCode 插件 baize-trace.js（utils.skillopt 随启动部署）注册
`tool.execute.before` 钩子：每次工具调用前 POST 本端点，命中 deny list
（action_expectations 中 mode='pre' 的行）时插件抛错阻断该次工具执行——
动作门禁从"终态核对该做的做了没"补上"不允许做的别做"。

- POST /pre-check  body {sessionId, tool, argsText}
    返回 {allow: bool, reason?: str}。allow=false 时插件阻断调用并把
    reason 回给模型。

鉴权：X-Internal-Token（与 ai_memory_internal / ai_subagent_internal 同一把
MCP_INTERNAL_TOKEN）。失败语义：本端点不可达/非 2xx 时插件侧静默放行
（可用性优先），终态核对仍兜底；查询异常时服务端同样 fail-open 并留痕。

sessionId 口径：插件看到的是 OpenCode 内部会话 id（ses_…）——根/批子会话
经 ai_chat_sessions.opencode_session_id 映射为平台会话 id（期望登记的
scope_id），子代理会话经 ai_chat_subtasks 归属根会话（deny list 登记在
根作用域，约束整棵树）。
"""
from flask import Blueprint, jsonify, request

from config import MCP_INTERNAL_TOKEN
from db import get_db
from utils.agent_ledger import pre_check_tool

ai_gate_internal_bp = Blueprint('ai_gate_internal', __name__,
                                url_prefix='/ai/gate/internal')


def _authorized():
    token = request.headers.get('X-Internal-Token', '')
    return bool(MCP_INTERNAL_TOKEN) and token == MCP_INTERNAL_TOKEN


def _resolve_scope_id(oc_or_platform_id: str) -> str:
    """工具调用所在会话 id → 期望登记的 scope_id。

    平台 id（sess_ 前缀）原样返回；OC 内部 id 先查根/批子会话表，再查
    子代理表（归属根会话——子代理动作受根上登记的 deny list 约束）；
    都查不到时原样返回（查不到就匹配不上 → allow，与无期望同义）。"""
    sid = oc_or_platform_id or ''
    if not sid or sid.startswith('sess_'):
        return sid
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ai_chat_sessions "
                    "WHERE opencode_session_id = %s "
                    "ORDER BY last_active_at DESC NULLS LAST LIMIT 1", (sid,))
                row = cur.fetchone()
                if row:
                    return row[0]
                cur.execute(
                    "SELECT root_session_id FROM ai_chat_subtasks "
                    "WHERE id = %s", (sid,))
                row = cur.fetchone()
                if row and row[0]:
                    return row[0]
    except Exception as e:  # noqa: BLE001 —— 映射失败按原 id 查（匹配不上 → allow）
        import logging
        logging.getLogger(__name__).warning(
            'pre-check session resolve failed %s: %s', sid, e)
    return sid


@ai_gate_internal_bp.route('/pre-check', methods=['POST'])
def pre_check():
    """工具调用前校验：session_id + tool + args → allow/deny。

    deny 条件：该会话存在 mode='pre' 的期望行，
    且 tool 匹配 + args_text ~ args_pattern。
    """
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(silent=True) or {}
    session_id = body.get('sessionId')
    tool = body.get('tool')
    args_text = body.get('argsText') or ''
    if not session_id or not tool:
        return jsonify({'allow': True}), 200
    scope_id = _resolve_scope_id(str(session_id))
    try:
        result = pre_check_tool(scope_id, str(tool), str(args_text or ''))
    except Exception as e:  # noqa: BLE001 —— 校验故障不阻断执行（fail-open）
        import logging
        logging.getLogger(__name__).warning(
            'pre-check failed sid=%s tool=%s: %s', scope_id, tool, e)
        result = {'allow': True, 'error': str(e)[:200]}
    return jsonify(result), 200
