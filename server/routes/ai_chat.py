"""AI chat blueprint (M1: sessions + messages + SSE + history).

Routes registered:
    POST   /ai/chat/sessions             create_session
    GET    /ai/chat/sessions/:id/messages history
    POST   /ai/chat/sessions/:id/messages send_message
    GET    /ai/chat/sessions/:id/events   sse_proxy
    DELETE /ai/chat/sessions/:id          delete_session
"""

import json
import secrets
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g as flask_g, Response, stream_with_context
from db import get_db
from auth import login_required, write_required
from utils.opencode_client import OpenCodeClient
from utils.workspace import create_session_workspace, cleanup_session_workspace
from utils.session_token import generate_token, revoke_token
from config import (
    AI_WORKSPACE_ROOT, OPENCODE_BASE_URL, MCP_SERVER_URL,
    AI_SESSION_TTL_HOURS,
)

ai_chat_bp = Blueprint('ai_chat', __name__, url_prefix='/ai/chat')


def _new_session_id() -> str:
    return 'sess_' + secrets.token_hex(6)


@ai_chat_bp.route('/sessions', methods=['POST'])
@write_required
def create_session():
    user = flask_g.current_user
    body = request.get_json(silent=True) or {}
    project_menu_id = body.get('projectMenuId')

    session_id = _new_session_id()
    workspace_path = create_session_workspace(
        AI_WORKSPACE_ROOT, user['userId'], session_id,
    )

    # 1) insert row (need a row before session_token utility can UPDATE it)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "(id, user_id, title, workspace_path, session_token, "
            " token_expires_at, project_menu_id, status) "
            "VALUES (%s, %s, %s, %s, %s, NOW() + INTERVAL '1 hour', %s, 'active')",
            (session_id, user['userId'], '新会话', workspace_path,
             '_pending_', project_menu_id),
        )
    # 2) overwrite the token via the dedicated utility (single source of truth for TTL math)
    token = generate_token(session_id, AI_SESSION_TTL_HOURS)

    # 3) ask OpenCode to start a session bound to this workspace
    client = OpenCodeClient(OPENCODE_BASE_URL)
    opencode_session_id = client.create_session(cwd=workspace_path, title='新会话')

    # 4) register our MCP server, scoped by token
    mcp_url = f"{MCP_SERVER_URL}/mcp?token={token}"
    client.register_mcp(session_id=opencode_session_id, url=mcp_url)

    # 5) persist opencode_session_id
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET opencode_session_id = %s WHERE id = %s",
            (opencode_session_id, session_id),
        )

    return jsonify({
        'id': session_id,
        'title': '新会话',
        'workspacePath': workspace_path,
    }), 201
