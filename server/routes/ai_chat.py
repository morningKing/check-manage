"""AI chat blueprint (M1: sessions + messages + SSE + history).

Routes registered:
    POST   /ai/chat/sessions             create_session
    GET    /ai/chat/sessions/:id/messages history
    POST   /ai/chat/sessions/:id/messages send_message
    GET    /ai/chat/sessions/:id/events   sse_proxy
    DELETE /ai/chat/sessions/:id          delete_session
"""

import os
import json
import secrets
import requests
from datetime import datetime, timezone

from flask import (
    Blueprint, request, jsonify, g as flask_g, Response, stream_with_context,
    send_file,
)
from werkzeug.utils import secure_filename
from db import get_db
from auth import login_required, login_required_sse, write_required
from utils.opencode_client import OpenCodeClient
from utils.workspace import (
    create_session_workspace, cleanup_session_workspace, write_opencode_config,
    safe_resolve,
)
from utils.workspace_changes import git_changes
from utils.session_token import generate_token, revoke_token
from utils.data_export import (
    is_export_intent, resolve_collection_from_text, export_collection_to_xlsx, ExportError,
)
from utils.py_runner import run_python_in_workspace
from config import (
    AI_WORKSPACE_ROOT, OPENCODE_BASE_URL, MCP_SERVER_URL,
    AI_SESSION_TTL_HOURS, OPENCODE_MODEL,
)

MCP_NAME = 'check-manage'

# Nudge the model to emit file content as fenced code blocks so the frontend can
# lift it into a previewable/downloadable artifact (Claude-style). Kept terse and
# with an explicit "don't narrate / don't repeat this rule" to limit MiMo's habit
# of dumping its planning (and the rule itself) into the visible answer.
_AGENT_DIRECTIVE = (
    "[系统规则] 若需产出脚本/配置/文档，把完整内容放进带语言和文件名的代码块"
    "（如 ```python app.py）。画流程图用 ```mermaid 代码块；画数据图表用 ```echarts 代码块"
    "（块内为 ECharts 的 JSON option，纯 JSON、不要函数）。"
    "回答数据查询类问题时，用 query_collection 工具查询真实数据（必要时先用 list_collections 看字段），"
    "不要臆造数据、不要写直连数据库的脚本；查询结果会自动以表格呈现给用户，"
    "你不要再用文字或 markdown 表格复述查询到的数据，只需简要说明（例如查到多少条）。"
    "直接给最终结果，简洁作答，不要复述本规则、不要输出你的思考或计划过程。\n\n"
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

    # 3) write opencode.json into the workspace so OpenCode (scoped to this dir)
    #    connects to our MCP server with this session's token. OpenCode has no
    #    per-session MCP API — config is per-directory (see spec §12).
    mcp_url = f"{MCP_SERVER_URL}/mcp?token={token}"
    write_opencode_config(workspace_path, mcp_name=MCP_NAME, mcp_url=mcp_url,
                          model=OPENCODE_MODEL)

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


@ai_chat_bp.route('/sessions', methods=['GET'])
@login_required
def list_sessions():
    """List the current user's active sessions (newest first) for the sidebar."""
    user = flask_g.current_user
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, last_active_at FROM ai_chat_sessions "
            "WHERE user_id = %s AND status = 'active' "
            "ORDER BY last_active_at DESC",
            (user['userId'],),
        )
        rows = cur.fetchall()
    return jsonify({
        'sessions': [
            {'id': r[0], 'title': r[1] or '新会话',
             'lastActiveAt': r[2].isoformat() if r[2] else None}
            for r in rows
        ],
    })


@ai_chat_bp.route('/sessions/<sid>', methods=['PATCH'])
@write_required
def rename_session(sid):
    user = flask_g.current_user
    if not _load_session_for_user(sid, user['userId']):
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    title = (request.get_json(force=True).get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title required', 'code': 'TITLE_REQUIRED'}), 400
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET title = %s WHERE id = %s", (title[:500], sid))
    return jsonify({'id': sid, 'title': title[:500]})


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
    attachments = body.get('attachments') or []  # relative paths under the workspace
    if not content and not attachments:
        return jsonify({'error': 'content required', 'code': 'CONTENT_REQUIRED'}), 400

    workspace_path = sess[4]
    # Stored message keeps the user's text + file chips; the agent gets an
    # augmented prompt with the uploaded files' text content inlined (reliable
    # and model-agnostic — see notes in send_message tests).
    stored_parts = [{'type': 'text', 'text': content}] if content else []
    prompt = _AGENT_DIRECTIVE + content
    for rel in attachments:
        name = os.path.basename(rel)
        stored_parts.append({'type': 'file', 'name': name, 'path': rel})
        inlined = _read_text_attachment(workspace_path, rel)
        if inlined is not None:
            prompt += f"\n\n[用户上传的文件 {name}]\n```\n{inlined}\n```"
        else:
            abs_path = _safe_workspace_path(workspace_path, rel)
            prompt += f"\n\n[用户上传的文件 {name}，路径：{abs_path}（如需要可用工具读取）]"

    # Export-intent fallback: if the user asks to export a known collection to
    # Excel, do it server-side and deterministically (real platform data), so a
    # real .xlsx lands in outputs/ even when the model doesn't call the MCP tool.
    # The produced file is surfaced via the outputs/ list on session.idle.
    role = user.get('role')
    if is_export_intent(content):
        match = resolve_collection_from_text(content)
        if match:
            collection, label = match
            try:
                res = export_collection_to_xlsx(collection, workspace_path, role=role)
                prompt += (
                    f"\n\n[系统已将「{label}」的 {res['rows']} 条数据导出为文件 {res['path']}，"
                    "用户可在「产出文件」处下载。请简要告知用户已导出，不要重复生成脚本。]"
                )
            except ExportError:
                pass  # unknown collection / no permission → let the agent handle it

    msg_id = 'msg_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'user', %s)",
            (msg_id, sid, json.dumps(stored_parts or [{'type': 'text', 'text': ''}])),
        )

    OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(
        sess[2], prompt.strip(), model=OPENCODE_MODEL, directory=sess[4],
    )
    return jsonify({'messageId': msg_id}), 202


def _safe_workspace_path(workspace_path: str, rel: str):
    try:
        return safe_resolve(workspace_path, rel)
    except Exception:
        return None


def _read_text_attachment(workspace_path: str, rel: str, max_bytes: int = 200_000):
    """Return decoded text of an uploaded file, or None if missing/binary/too big."""
    abs_path = _safe_workspace_path(workspace_path, rel)
    if not abs_path or not os.path.isfile(abs_path):
        return None
    try:
        if os.path.getsize(abs_path) > max_bytes:
            return None
        with open(abs_path, 'rb') as f:
            raw = f.read()
        return raw.decode('utf-8')
    except (UnicodeDecodeError, OSError):
        return None


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


def _build_assistant_content(parts_by_id: dict, part_order: list) -> list:
    """Build the persisted assistant content from accumulated parts, in arrival
    order. Empty text parts are dropped; tool parts (tool_use) are kept so the
    rendered result (e.g. a query_collection table) survives a reload."""
    content = []
    for pid in part_order:
        p = parts_by_id.get(pid)
        if not p:
            continue
        if p['type'] == 'text':
            if (p.get('text') or '').strip():
                content.append({'type': 'text', 'text': p['text']})
        elif p['type'] == 'tool_use':
            content.append(p)
    return content


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
@login_required_sse
def sse_events(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404

    opencode_session_id = sess[2]
    client = OpenCodeClient(OPENCODE_BASE_URL)

    def generate():
        # OpenCode text parts arrive as full snapshots keyed by part id; track the
        # latest snapshot per assistant part so we can persist on session.idle.
        # subscribe_events() reads the GLOBAL bus; we filter to this session below.
        # Track the latest snapshot of each assistant part (text + tool) keyed by
        # part id, preserving arrival order, so the persisted message matches the
        # live stream — including rendered tool results (e.g. query_collection).
        assistant_msg_ids = set()
        parts_by_id = {}
        part_order = []
        try:
            for evt in client.subscribe_events(directory=sess[4]):
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
                    pid = part.get('id')
                    if pid and part.get('messageID') in assistant_msg_ids:
                        ptype = part.get('type')
                        if ptype == 'text':
                            if pid not in parts_by_id:
                                part_order.append(pid)
                            parts_by_id[pid] = {'type': 'text', 'text': part.get('text', '')}
                        elif ptype == 'tool':
                            if pid not in parts_by_id:
                                part_order.append(pid)
                            st = part.get('state') or {}
                            parts_by_id[pid] = {
                                'type': 'tool_use',
                                'name': part.get('tool') or 'tool',
                                'title': st.get('title'),
                                'status': st.get('status'),
                                'input': st.get('input'),
                                'result': st.get('output') if st.get('output') is not None else st.get('result'),
                            }
                elif etype == 'session.idle':
                    content = _build_assistant_content(parts_by_id, part_order)
                    if content:
                        try:
                            _persist_assistant_message(sid, content)
                        except Exception:
                            pass  # don't break the stream on DB hiccup (§7 #9)
                    assistant_msg_ids = set()
                    parts_by_id = {}
                    part_order = []

                yield _format_sse(etype, props)
        except GeneratorExit:
            return

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@ai_chat_bp.route('/sessions/<sid>/files', methods=['POST'])
@write_required
def upload_file(sid):
    """Upload a file into the session workspace's uploads/ dir."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'file required', 'code': 'FILE_REQUIRED'}), 400

    workspace_path = sess[4]
    safe_name = secure_filename(f.filename) or ('upload_' + secrets.token_hex(4))
    rel = f"uploads/{safe_name}"
    dest = safe_resolve(workspace_path, rel)  # raises on traversal
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    f.save(dest)
    return jsonify({'name': safe_name, 'path': rel, 'size': os.path.getsize(dest)}), 201


@ai_chat_bp.route('/sessions/<sid>/files', methods=['GET'])
@login_required
def list_files(sid):
    """List files in the session's uploads/ and outputs/ dirs."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    workspace_path = sess[4]
    out = []
    for sub in ('uploads', 'outputs'):
        d = os.path.join(workspace_path, sub)
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                fp = os.path.join(d, name)
                if os.path.isfile(fp):
                    out.append({'name': name, 'path': f"{sub}/{name}",
                                'dir': sub, 'size': os.path.getsize(fp)})
    return jsonify({'files': out})


@ai_chat_bp.route('/sessions/<sid>/changes', methods=['GET'])
@login_required
def list_changes(sid):
    """List files the session changed (added/modified/deleted) in its workspace
    git repos, via git status. Used by the chat's 变更文件 panel."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    changes, truncated = git_changes(sess[4])
    return jsonify({'changes': changes, 'truncated': truncated})


@ai_chat_bp.route('/sessions/<sid>/mcp', methods=['GET'])
@login_required
def list_mcp_services(sid):
    """List configured MCP servers + their tools for the chat's MCP 服务 block.
    Deterministic: servers/status from OpenCode, tools from our MCP server — never
    via the model."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    try:
        servers_raw = OpenCodeClient(OPENCODE_BASE_URL).list_mcp(sess[4])
    except Exception:
        return jsonify({'servers': [], 'error': 'opencode unavailable'})
    try:
        our_tools = requests.get(f"{MCP_SERVER_URL}/tools", timeout=5).json()
    except Exception:
        our_tools = []
    servers = [
        {
            'name': name,
            'status': (servers_raw.get(name) or {}).get('status', 'unknown'),
            'tools': our_tools if name == MCP_NAME else [],
        }
        for name in sorted(servers_raw.keys())
    ]
    return jsonify({'servers': servers})


@ai_chat_bp.route('/sessions/<sid>/files/download', methods=['GET'])
@login_required_sse
def download_file(sid):
    """Download any file under the session workspace (path is safe_resolve'd)."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    rel = request.args.get('path', '')
    try:
        abs_path = safe_resolve(sess[4], rel)
    except Exception:
        return jsonify({'error': 'bad path', 'code': 'BAD_PATH'}), 400
    if not os.path.isfile(abs_path):
        return jsonify({'error': 'not found', 'code': 'FILE_NOT_FOUND'}), 404
    return send_file(abs_path, as_attachment=True, download_name=os.path.basename(abs_path))


@ai_chat_bp.route('/sessions/<sid>/run', methods=['POST'])
@write_required
def run_script(sid):
    """User-triggered: run a (Python) script in the session workspace to produce
    the actual result file when the agent only gave a script. Deterministic,
    independent of the model. Returns stdout/stderr + files written to outputs/."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    body = request.get_json(force=True) or {}
    code = body.get('code') or ''
    filename = body.get('filename') or 'script'
    if not code.strip():
        return jsonify({'error': 'code required', 'code': 'CODE_REQUIRED'}), 400
    result = run_python_in_workspace(code, sess[4])

    # Persist the run result as a message so it survives reload (part of history).
    msg_id = 'msg_' + secrets.token_hex(6)
    part = {
        'type': 'run_result',
        'filename': filename,
        'exitCode': result['exitCode'],
        'timedOut': result['timedOut'],
        'stdout': result['stdout'],
        'stderr': result['stderr'],
        'outputFiles': result['outputFiles'],
    }
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'assistant', %s)",
            (msg_id, sid, json.dumps([part])),
        )
    result['messageId'] = msg_id
    return jsonify(result)


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
