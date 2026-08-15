"""Admin session management API — unified cross-type session view.

Endpoints (all require admin.ai_chat_admin):
  GET  /ai/chat/admin/sessions/v2          — paginated list with filters
  GET  /ai/chat/admin/sessions/v2/<sid>    — session detail
  GET  /ai/chat/admin/sessions/v2/<sid>/messages — conversation history
  GET  /ai/chat/admin/sessions/v2/<sid>/files    — workspace file list
  GET  /ai/chat/admin/sessions/v2/<sid>/files/download — download file
"""
import os

from flask import Blueprint, jsonify, request
from auth import require_permission, require_permission_sse
from utils.session_admin_repo import (
    admin_get_session_detail,
    admin_get_session_messages,
    admin_list_sessions_v2,
    VALID_STATUSES,
    VALID_SOURCE_TYPES,
)

ai_session_admin_bp = Blueprint(
    'ai_session_admin', __name__,
    url_prefix='/ai/chat/admin/sessions/v2')


def _row_to_session(r: dict) -> dict:
    """Convert a DB row (snake_case) to the API contract (camelCase)."""
    return {
        'id': r['id'],
        'userId': r.get('user_id'),
        'username': r.get('username'),
        'title': r.get('title'),
        'status': r.get('status'),
        'sourceType': r.get('source_type'),
        'batchId': r.get('batch_id'),
        'batchName': r.get('batch_name'),
        'batchSeq': r.get('batch_seq'),
        'inputFile': r.get('batch_input_file'),
        'scanTaskId': r.get('scan_task_id'),
        'lastMessagePreview': r.get('last_message_preview'),
        'errorMessage': r.get('error_message'),
        'createdAt': r['created_at'].isoformat() if r.get('created_at') else None,
        'lastActiveAt': r['last_active_at'].isoformat() if r.get('last_active_at') else None,
        'opencodeSessionId': r.get('opencode_session_id'),
    }


def _row_to_detail(r: dict) -> dict:
    """Convert a detail DB row to the API contract."""
    d = _row_to_session(r)
    d['workspacePath'] = r.get('workspace_path')
    d['batchApiKeyId'] = r.get('batch_api_key_id')
    return d


@ai_session_admin_bp.get('')
@require_permission('admin.ai_chat_admin')
def list_sessions():
    """Unified paginated session list with optional filters."""
    try:
        page = max(1, int(request.args.get('page', 1)))
        page_size = min(max(1, int(request.args.get('pageSize', 20))), 100)
    except (TypeError, ValueError):
        return jsonify({'error': 'page 与 pageSize 必须是整数'}), 400

    status = request.args.get('status', '').strip() or None
    source_type = request.args.get('sourceType', '').strip() or None
    owner = request.args.get('owner', '').strip() or None
    keyword = request.args.get('keyword', '').strip() or None
    batch_id = request.args.get('batchId', '').strip() or None

    if status and status not in VALID_STATUSES:
        return jsonify({'error': f'无效状态: {status}'}), 400
    if source_type and source_type not in VALID_SOURCE_TYPES:
        return jsonify({'error': f'无效来源类型: {source_type}'}), 400

    result = admin_list_sessions_v2(
        page=page, page_size=page_size,
        status=status, source_type=source_type,
        owner=owner, keyword=keyword, batch_id=batch_id,
    )
    result['items'] = [_row_to_session(r) for r in result['items']]
    return jsonify(result)


@ai_session_admin_bp.get('/<session_id>')
@require_permission('admin.ai_chat_admin')
def session_detail(session_id):
    """Single session detail with all columns."""
    detail = admin_get_session_detail(session_id)
    if not detail:
        return jsonify({'error': '会话不存在'}), 404
    return jsonify(_row_to_detail(detail))


@ai_session_admin_bp.get('/<session_id>/messages')
@require_permission('admin.ai_chat_admin')
def session_messages(session_id):
    """Conversation history for any session (admin context, no ownership check)."""
    detail = admin_get_session_detail(session_id)
    if not detail:
        return jsonify({'error': '会话不存在'}), 404
    result = admin_get_session_messages(session_id)
    return jsonify(result)


@ai_session_admin_bp.get('/<session_id>/files')
@require_permission_sse('admin.ai_chat_admin')
def session_files(session_id):
    """Workspace file list for any session."""
    from utils.workspace_outputs import list_session_files, augment_with_data_file_id
    detail = admin_get_session_detail(session_id)
    if not detail:
        return jsonify({'error': '会话不存在'}), 404
    ws = detail.get('workspace_path')
    if not ws:
        return jsonify({'files': [], 'truncated': False})
    files, truncated = list_session_files(ws)
    augment_with_data_file_id(session_id, files)
    return jsonify({'files': files, 'truncated': truncated})


@ai_session_admin_bp.get('/<session_id>/files/download')
@require_permission_sse('admin.ai_chat_admin')
def session_file_download(session_id):
    """Download a single file from session workspace."""
    from flask import send_file
    detail = admin_get_session_detail(session_id)
    if not detail:
        return jsonify({'error': '会话不存在'}), 404
    ws = detail.get('workspace_path')
    if not ws:
        return jsonify({'error': '该会话没有工作区'}), 400
    rel_path = request.args.get('path', '').strip()
    if not rel_path:
        return jsonify({'error': 'path 参数必填'}), 400
    normalized = os.path.normpath(rel_path)
    if '..' in normalized.split(os.sep):
        return jsonify({'error': '路径非法'}), 400
    abs_path = os.path.join(ws, normalized)
    if not os.path.commonpath([ws, abs_path]).startswith(ws):
        return jsonify({'error': '路径非法'}), 400
    if not os.path.isfile(abs_path):
        return jsonify({'error': '文件不存在'}), 404
    return send_file(abs_path, as_attachment=True)
