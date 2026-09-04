"""Admin session management API — unified cross-type session view.

Endpoints (all require admin.ai_chat_admin):
  GET  /ai/chat/admin/sessions/v2          — paginated list with filters
  GET  /ai/chat/admin/sessions/v2/<sid>    — session detail
  GET  /ai/chat/admin/sessions/v2/<sid>/messages — conversation history
  GET  /ai/chat/admin/sessions/v2/<sid>/files    — workspace file list
  GET  /ai/chat/admin/sessions/v2/<sid>/files/download — download file
  POST /ai/chat/admin/sessions/v2/<sid>/analyze  — trigger trace analysis
"""
import os
import secrets
import logging

from flask import Blueprint, g as flask_g, jsonify, request
from db import get_db
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


@ai_session_admin_bp.post('/<session_id>/analyze')
@require_permission('admin.ai_chat_admin')
def analyze_session(session_id):
    """Trigger trace analysis for a session.

    Creates a new analysis session with the trace-analyzer skill injected,
    sends an analysis prompt, and returns the new session ID.
    """
    from config import AI_WORKSPACE_ROOT, OPENCODE_BASE_URL, MCP_SERVER_URL, get_default_chat_model
    from utils.opencode_client import OpenCodeClient
    from utils.workspace import create_session_workspace, write_opencode_config
    from utils.session_token import generate_token
    from utils.mcp_servers import enabled_mcp_config

    MCP_NAME = 'check-manage'
    logger = logging.getLogger('ai_session_admin')

    # 1. Verify target session exists
    detail = admin_get_session_detail(session_id)
    if not detail:
        return jsonify({'error': '会话不存在'}), 404

    user = flask_g.current_user
    user_id = user['userId']

    # 2. Create analysis session
    analysis_sid = 'sess_' + secrets.token_hex(6)
    workspace_path = create_session_workspace(AI_WORKSPACE_ROOT, user_id, analysis_sid)

    # 3. Insert DB row
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "(id, user_id, title, workspace_path, session_token, "
            " token_expires_at, status) "
            "VALUES (%s, %s, %s, %s, %s, NOW() + INTERVAL '1 hour', 'active')",
            (analysis_sid, user_id, f'轨迹分析: {session_id}', workspace_path, '_pending_'),
        )

    # 4. Generate token + write opencode.json
    token = generate_token(analysis_sid, 24)
    mcp_url = f"{MCP_SERVER_URL}/mcp?token={token}"
    extra_mcp = enabled_mcp_config(reserved_names=[MCP_NAME])
    write_opencode_config(
        workspace_path, mcp_name=MCP_NAME, mcp_url=mcp_url,
        model=get_default_chat_model(), extra_mcp=extra_mcp,
    )

    # 5. Inject global skills (including trace-analyzer)
    try:
        from utils.global_skills import inject_global_skills
        inject_global_skills(workspace_path, AI_WORKSPACE_ROOT)
    except Exception:
        pass

    # 6. Create OpenCode session
    client = OpenCodeClient(OPENCODE_BASE_URL)
    try:
        oc_sid = client.create_session(
            directory=workspace_path,
            title=f'轨迹分析: {session_id}',
        )
    except Exception as e:
        logger.warning('analyze: OpenCode create_session failed: %s', e)
        return jsonify({'error': f'OpenCode 会话创建失败: {e}'}), 502

    # 7. Persist opencode_session_id
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET opencode_session_id = %s WHERE id = %s",
            (oc_sid, analysis_sid),
        )

    # 8. Build analysis prompt
    source_info = ''
    if detail.get('scan_task_id'):
        source_info = f'扫描任务 {detail.get("scan_task_name") or detail["scan_task_id"]}'
    elif detail.get('batch_id'):
        source_info = f'批任务 {detail.get("batch_name") or detail["batch_id"]}'
    else:
        source_info = '交互会话'

    analysis_prompt = (
        f'使用 `trace-analyzer` 技能: '
        f'分析会话 {session_id} 的执行轨迹。\n\n'
        f'## 会话概况\n'
        f'- 来源: {source_info}\n'
        f'- 状态: {detail.get("status", "未知")}\n'
        f'- Agent: {detail.get("agent") or detail.get("batch_agent") or "默认"}\n'
    )
    if detail.get('error_message'):
        analysis_prompt += f'- 错误信息: {detail["error_message"]}\n'

    # 9. Persist user message
    msg_id = 'msg_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'user', %s)",
            (msg_id, analysis_sid,
             '[{"type": "text", "text": ' + __import__('json').dumps(analysis_prompt) + '}]'),
        )

    # 10. Send prompt to OpenCode
    try:
        client.send_prompt_async(oc_sid, analysis_prompt, directory=workspace_path)
    except Exception as e:
        logger.warning('analyze: send_prompt_async failed: %s', e)
        return jsonify({'error': f'发送分析请求失败: {e}'}), 502

    logger.info('analyze: triggered for %s -> analysis session %s (oc=%s)',
                session_id, analysis_sid, oc_sid)

    return jsonify({
        'analysisSessionId': analysis_sid,
        'message': f'已触发轨迹分析，分析会话: {analysis_sid}',
    })
