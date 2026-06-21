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
    add_memory(body.get('userId', ''), body.get('messages') or [])
    return jsonify({'ok': True})


@ai_memory_internal_bp.route('/delete', methods=['POST'])
def internal_delete():
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(force=True) or {}
    delete_memory(body.get('memoryId', ''))
    return jsonify({'ok': True})
