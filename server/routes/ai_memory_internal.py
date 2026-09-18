"""Internal memory endpoints for the MCP server (NOT for browsers).

The MCP server runs in its own process; letting it open mem0/Chroma directly
would double-write Chroma's single-writer store. So memory ops route here and
Flask stays the sole owner. Guarded by a shared MCP_INTERNAL_TOKEN.
"""
from flask import Blueprint, request, jsonify
from config import MCP_INTERNAL_TOKEN
from utils.memory import search_memory, add_memory, delete_memory

ai_memory_internal_bp = Blueprint('ai_memory_internal', __name__, url_prefix='/ai/memory/internal')


def _authorized():
    token = request.headers.get('X-Internal-Token', '')
    return bool(MCP_INTERNAL_TOKEN) and token == MCP_INTERNAL_TOKEN


@ai_memory_internal_bp.route('/search', methods=['POST'])
def internal_search():
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(force=True) or {}
    user_id = body.get('userId', '')
    query = body.get('query', '')
    limit = int(body.get('limit', 5))
    return jsonify({'results': search_memory(user_id, query, limit)})


@ai_memory_internal_bp.route('/add', methods=['POST'])
def internal_add():
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(force=True) or {}
    # 来源标记（P2 §7.4）：batch 路径显式传 source='batch'，默认对话提取
    add_memory(body.get('userId', ''), body.get('messages') or [],
               source=str(body.get('source') or 'batch'))
    return jsonify({'ok': True})


@ai_memory_internal_bp.route('/delete', methods=['POST'])
def internal_delete():
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(force=True) or {}
    delete_memory(body.get('memoryId', ''))
    return jsonify({'ok': True})


@ai_memory_internal_bp.route('/runtime-events', methods=['POST'])
def runtime_events():
    """OpenCode 插件（baize-trace.js）上报 skill load/invoke 与会话收敛事件。
    internal token 鉴权（与记忆内部通道同一信任边界）。"""
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(force=True) or {}
    kind = body.get('kind') or 'skill'
    from utils import skillopt
    if kind == 'skill':
        result = skillopt.record_runtime_skill_event(body)
        return jsonify(result)
    if kind == 'session.idle':
        skillopt.mark_session_idle(body.get('sessionID') or '')
        return jsonify({'ok': True})
    return jsonify({'error': 'unknown kind'}), 400
