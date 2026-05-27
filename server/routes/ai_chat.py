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
from utils.workspace import (
    create_session_workspace, cleanup_session_workspace, write_opencode_config,
)
from utils.session_token import generate_token, revoke_token
from config import (
    AI_WORKSPACE_ROOT, OPENCODE_BASE_URL, MCP_SERVER_URL,
    AI_SESSION_TTL_HOURS,
)

MCP_NAME = 'check-manage'

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

    # 3) write opencode.json into the workspace so OpenCode (scoped to this dir)
    #    connects to our MCP server with this session's token. OpenCode has no
    #    per-session MCP API — config is per-directory (see spec §12).
    mcp_url = f"{MCP_SERVER_URL}/mcp?token={token}"
    write_opencode_config(workspace_path, mcp_name=MCP_NAME, mcp_url=mcp_url)

    # 4) ask OpenCode to start a session bound to this workspace (directory query param)
    client = OpenCodeClient(OPENCODE_BASE_URL)
    opencode_session_id = client.create_session(directory=workspace_path, title='新会话')

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


def _load_session_for_user(session_id: str, user_id: str):
    """Return (id, user_id, opencode_session_id, status, workspace_path) or None."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, opencode_session_id, status, workspace_path "
            "FROM ai_chat_sessions "
            "WHERE id = %s AND user_id = %s",
            (session_id, user_id),
        )
        return cur.fetchone()


@ai_chat_bp.route('/sessions/<sid>/messages', methods=['POST'])
@write_required
def send_message(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404

    body = request.get_json(force=True)
    content = (body.get('content') or '').strip()
    if not content:
        return jsonify({'error': 'content required', 'code': 'CONTENT_REQUIRED'}), 400

    msg_id = 'msg_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'user', %s)",
            (msg_id, sid, json.dumps([{'type': 'text', 'text': content}])),
        )

    OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(sess[2], content)
    return jsonify({'messageId': msg_id}), 202


@ai_chat_bp.route('/sessions/<sid>/messages', methods=['GET'])
@login_required
def get_messages(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404

    since = request.args.get('since')
    with get_db() as conn:
        cur = conn.cursor()
        if since:
            cur.execute(
                "SELECT id, role, content, created_at FROM ai_chat_messages "
                "WHERE session_id = %s AND id > %s "
                "ORDER BY created_at ASC",
                (sid, since),
            )
        else:
            cur.execute(
                "SELECT id, role, content, created_at FROM ai_chat_messages "
                "WHERE session_id = %s ORDER BY created_at ASC",
                (sid,),
            )
        rows = cur.fetchall()

    return jsonify({
        'messages': [
            {'id': r[0], 'role': r[1], 'content': r[2],
             'createdAt': r[3].isoformat() if r[3] else None}
            for r in rows
        ],
    })


def _format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _persist_assistant_message(session_id: str, content_parts: list) -> None:
    """Persist a completed assistant message (called on session.idle)."""
    msg_id = 'msg_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'assistant', %s)",
            (msg_id, session_id, json.dumps(content_parts)),
        )


def _event_session_id(props: dict):
    """OpenCode puts the session id in different nested spots per event type."""
    if not isinstance(props, dict):
        return None
    if props.get('sessionID'):
        return props['sessionID']
    for k in ('part', 'info'):
        v = props.get(k)
        if isinstance(v, dict) and v.get('sessionID'):
            return v['sessionID']
    return None


@ai_chat_bp.route('/sessions/<sid>/events', methods=['GET'])
@login_required
def sse_events(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404

    opencode_session_id = sess[2]
    workspace_path = sess[4]
    client = OpenCodeClient(OPENCODE_BASE_URL)

    def generate():
        # OpenCode text parts arrive as full snapshots keyed by part id; track the
        # latest snapshot per assistant part so we can persist on session.idle.
        assistant_msg_ids = set()
        text_by_part = {}
        part_order = []
        try:
            for evt in client.subscribe_events(directory=workspace_path):
                etype = evt.get('event', '')
                obj = evt.get('data') or {}
                props = obj.get('properties') or {}

                ev_sid = _event_session_id(props)
                if ev_sid and ev_sid != opencode_session_id:
                    continue

                if etype == 'message.updated':
                    info = props.get('info') or {}
                    if info.get('role') == 'assistant' and info.get('id'):
                        assistant_msg_ids.add(info['id'])
                elif etype == 'message.part.updated':
                    part = props.get('part') or {}
                    if part.get('type') == 'text' and part.get('messageID') in assistant_msg_ids:
                        pid = part.get('id')
                        if pid not in text_by_part:
                            part_order.append(pid)
                        text_by_part[pid] = part.get('text', '')
                elif etype == 'session.idle':
                    text = ''.join(text_by_part[p] for p in part_order)
                    if text.strip():
                        try:
                            _persist_assistant_message(sid, [{'type': 'text', 'text': text}])
                        except Exception:
                            pass  # don't break the stream on DB hiccup (§7 #9)
                    assistant_msg_ids = set()
                    text_by_part = {}
                    part_order = []

                yield _format_sse(etype, props)
        except GeneratorExit:
            return

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@ai_chat_bp.route('/sessions/<sid>', methods=['DELETE'])
@write_required
def delete_session(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404

    opencode_session_id = sess[2]
    if opencode_session_id:
        try:
            OpenCodeClient(OPENCODE_BASE_URL).delete_session(opencode_session_id)
        except Exception:
            pass  # 404 from OpenCode = already gone (§7 #11)

    # Security-critical first: kill the token and mark the session dead so a
    # failure to remove files (e.g. Windows handle held by OpenCode) can't leave
    # an authenticated session alive.
    revoke_token(sid)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET status = 'deleted' WHERE id = %s",
            (sid,),
        )

    cleanup_session_workspace(AI_WORKSPACE_ROOT, user['userId'], sid)  # best-effort
    return '', 204
