"""AI chat blueprint: sessions + messages + SSE + history, plus files,
workspace changes, MCP services, and command-palette endpoints.

Routes registered:
    POST   /ai/chat/sessions              create_session
    GET    /ai/chat/sessions/:id/messages history
    POST   /ai/chat/sessions/:id/messages send_message
    GET    /ai/chat/sessions/:id/events   sse_proxy
    POST   /ai/chat/sessions/:id/files    upload; GET .../files, .../files/download
    POST   /ai/chat/sessions/:id/skills   install a skill zip
    GET    /ai/chat/sessions/:id/changes  workspace git changes
    POST   /ai/chat/sessions/:id/run      run a python artifact
    GET    /ai/chat/sessions/:id/mcp      list MCP servers + tools
    GET    /ai/chat/sessions/:id/commands list OpenCode commands + skills
    POST   /ai/chat/sessions/:id/command  run an OpenCode command
    POST   /ai/chat/sessions/:id/abort    abort the in-flight turn
    POST   /ai/chat/sessions/:id/close    close_session
    POST   /ai/chat/sessions/:id/reopen   reopen_session
    DELETE /ai/chat/sessions/:id/messages/:msg_id  drop a message + everything after
    GET    /ai/chat/agents                list_agents
"""

import os
import json
import secrets
import requests
from datetime import datetime, timezone, timedelta

from flask import (
    Blueprint, request, jsonify, g as flask_g, Response, stream_with_context,
    send_file,
)
from utils.filename import safe_filename
from db import get_db
from auth import login_required, login_required_sse, write_required, require_permission
from utils.opencode_client import OpenCodeClient
from utils.workspace import (
    create_session_workspace, write_opencode_config,
    safe_resolve,
)
from utils.workspace_changes import git_changes, file_diff
from utils.chat_persist import (
    ensure_listener, stop_listener, new_state, apply_event, persist_turn, event_session_id,
)
from utils.session_token import generate_token
from utils.data_export import (
    is_export_intent, resolve_collection_from_text, export_collection_to_xlsx, ExportError,
)
from utils.py_runner import run_python_in_workspace
from utils.skill_upload import extract_skill_zip, SkillUploadError
from utils.memory import search_memory, render_memory_block
from utils.operation_log import log_operation
from config import (
    AI_WORKSPACE_ROOT, OPENCODE_BASE_URL, MCP_SERVER_URL,
    AI_SESSION_TTL_HOURS, OPENCODE_MODEL,
)

MCP_NAME = 'check-manage'

# OpenCode 内部系统 agent（mode=primary 但不应出现在用户选择器）。
# 这是按名字硬编码的假设（OpenCode 未给内部 agent 单独标识字段）；
# 升级 OpenCode 时如新增内部 primary agent 需在此补充。verified: opencode v1.2.x
INTERNAL_AGENTS = {'compaction', 'title', 'summary'}

# Nudge the model to emit file content as fenced code blocks so the frontend can
# lift it into a previewable/downloadable artifact (Claude-style). Kept terse and
# with an explicit "don't narrate / don't repeat this rule" to limit some models'
# habit of dumping their planning (and the rule itself) into the visible answer.
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

    log_operation('create', 'ai_chat_session', session_id, '新会话', '创建会话')

    return jsonify({
        'id': session_id,
        'title': '新会话',
        'workspacePath': workspace_path,
    }), 201


@ai_chat_bp.route('/models', methods=['GET'])
@login_required
def list_models():
    """List available LLM models for the composer dropdown.

    Flattens OpenCode's /provider response into:
      { "models": [{ "id": "<providerID>/<modelID>", "label": "<provider> / <model>",
                     "providerID": "...", "modelID": "...", "connected": bool }],
        "default": "<configured_default>" or empty string,
        "openCodeDefaults": { providerID: modelID } }
    Only connected providers are surfaced. `default` is the server-side
    OPENCODE_MODEL config; the picker uses it as the "Default" option label.
    """
    try:
        provider_info = OpenCodeClient(OPENCODE_BASE_URL).list_providers()
    except Exception as e:
        return jsonify({'error': f'OpenCode unreachable: {e}', 'models': []}), 502

    connected = provider_info.get('connected') or {}
    if isinstance(connected, list):
        connected_set = set(connected)
    else:
        connected_set = {pid for pid, ok in connected.items() if ok}

    models = []
    for prov in provider_info.get('all') or []:
        pid = prov.get('id')
        pname = prov.get('name') or pid
        if pid not in connected_set:
            continue
        prov_models = prov.get('models') or {}
        for mid, mdef in prov_models.items():
            models.append({
                'id': f'{pid}/{mid}',
                'label': f'{pname} / {mdef.get("name") or mid}',
                'providerID': pid,
                'modelID': mid,
                'connected': True,
            })
    # stable ordering for the UI: provider name then model name
    models.sort(key=lambda m: (m['label'].lower(), m['id']))
    return jsonify({
        'models': models,
        'default': OPENCODE_MODEL or '',
        'openCodeDefaults': provider_info.get('default') or {},
    })


@ai_chat_bp.route('/agents', methods=['GET'])
@login_required
def list_agents():
    """List user-facing primary OpenCode agents for the composer dropdown.

    Returns { "agents": [{name,description}] (primary), "subagents": [{name,description}],
              "default": "<name>"|null }.
    Filters to mode=='primary'/'subagent' and excludes OpenCode's internal agents.
    """
    try:
        raw = OpenCodeClient(OPENCODE_BASE_URL).list_agents()
    except Exception as e:
        return jsonify({'error': f'OpenCode unreachable: {e}', 'agents': [], 'subagents': [], 'default': None}), 502

    agents = [
        {'name': a.get('name'), 'description': a.get('description') or ''}
        for a in (raw or [])
        if a.get('mode') == 'primary' and a.get('name') not in INTERNAL_AGENTS
    ]
    subagents = [
        {'name': a.get('name'), 'description': a.get('description') or ''}
        for a in (raw or [])
        if a.get('mode') == 'subagent' and a.get('name') not in INTERNAL_AGENTS
    ]
    names = {a['name'] for a in agents}
    default = 'build' if 'build' in names else (agents[0]['name'] if agents else None)
    return jsonify({'agents': agents, 'subagents': subagents, 'default': default})


@ai_chat_bp.route('/sessions', methods=['GET'])
@login_required
def list_sessions():
    """List the current user's sessions for the sidebar (newest first).

    Includes both regular `active` sessions and batch-child sessions (whose
    status is one of pending/running/completed/failed). Batch children must
    show up so the 查看 button on the batch dashboard can switch into them.
    For batch children we synthesize a title from the input file basename
    so the user can distinguish them in the list.
    """
    user = flask_g.current_user
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, last_active_at, batch_id, batch_input_file, status "
            "FROM ai_chat_sessions "
            "WHERE user_id = %s "
            "  AND (status IN ('active', 'closed') OR batch_id IS NOT NULL) "
            "ORDER BY last_active_at DESC NULLS LAST, id DESC",
            (user['userId'],),
        )
        rows = cur.fetchall()

    def _title(stored_title, batch_id, batch_input_file):
        if stored_title:
            return stored_title
        if batch_id and batch_input_file:
            basename = batch_input_file.rsplit('/', 1)[-1]
            return f'[批] {basename}'
        return '新会话'

    return jsonify({
        'sessions': [
            {'id': r[0],
             'title': _title(r[1], r[3], r[4]),
             'lastActiveAt': r[2].isoformat() if r[2] else None,
             'status': r[5]}
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
    """Return (id, user_id, opencode_session_id, status, workspace_path) or None.

    On a successful load, also bump `last_active_at` and extend `token_expires_at`
    by `AI_SESSION_TTL_HOURS` — every user action keeps the session's MCP token
    alive so the chat doesn't 401 partway through a long day, and feeds the
    sidebar's recency sort (which previously only saw creation time).
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, opencode_session_id, status, workspace_path "
            "FROM ai_chat_sessions "
            "WHERE id = %s AND user_id = %s",
            (session_id, user_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute(
            "UPDATE ai_chat_sessions "
            "SET last_active_at = NOW(), token_expires_at = NOW() + %s "
            "WHERE id = %s",
            (timedelta(hours=AI_SESSION_TTL_HOURS), session_id),
        )
        return row


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
    mem_block = ''
    if content:
        mems = search_memory(user['userId'], content, limit=5)
        mem_block = render_memory_block(mems)
    prompt = _AGENT_DIRECTIVE + mem_block + content
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

    # Per-message model override: composer dropdown sends a `model` field
    # ("<providerID>/<modelID>"). Empty/missing falls back to OPENCODE_MODEL
    # (which itself may be empty, in which case OpenCode picks its default).
    requested_model = (body.get('model') or '').strip()
    effective_model = requested_model or OPENCODE_MODEL
    requested_agent = (body.get('agent') or '').strip()
    agent_mentions = body.get('agentMentions')
    if not isinstance(agent_mentions, list):
        agent_mentions = []
    import requests as _requests
    client = OpenCodeClient(OPENCODE_BASE_URL)
    oc_sid = sess[2]
    try:
        client.send_prompt_async(
            oc_sid, prompt.strip(), model=effective_model, directory=sess[4],
            agent=requested_agent, agent_parts=agent_mentions,
        )
    except _requests.RequestException:
        oc_sid = _recover_session_and_resend(
            client, sid, sess[4], msg_id, prompt.strip(),
            effective_model, requested_agent, agent_mentions,
        )
    ensure_listener(sid, oc_sid, sess[4])
    return jsonify({
        'messageId': msg_id,
        'model': effective_model or None,
        'agent': requested_agent or None,
    }), 202


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


def _render_history_block(sid, exclude_msg_id, max_turns=6):
    """最近 max_turns*2 条消息（不含当前这条）渲染成纯文本摘要，供会话复活时重注上下文。"""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, role, content FROM ai_chat_messages "
                "WHERE session_id=%s AND id != %s "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (sid, exclude_msg_id, max_turns * 2),
            )
            rows = cur.fetchall()
    except Exception:
        return ''
    if not rows:
        return ''
    rows = list(reversed(rows))
    lines = []
    for _id, role, content in rows:
        text = ''
        if isinstance(content, list):
            text = '\n'.join(p.get('text', '') for p in content
                             if isinstance(p, dict) and p.get('type') == 'text').strip()
        if not text:
            continue
        who = '用户' if role == 'user' else '助手'
        lines.append(f'{who}: {text}')
    if not lines:
        return ''
    return '[此前对话摘要（会话已恢复，供你延续上下文）]\n' + '\n'.join(lines) + '\n\n'


def _recover_session_and_resend(client, sid, workspace_path, current_msg_id,
                                prompt, model, agent, agent_parts):
    """OpenCode session 失效时：新建 session + 注入历史 + 更新绑定 + 重发。返回新的 opencode_session_id。"""
    new_oc = client.create_session(directory=workspace_path, title='恢复会话')
    history = _render_history_block(sid, exclude_msg_id=current_msg_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET opencode_session_id=%s WHERE id=%s", (new_oc, sid))
    client.send_prompt_async(new_oc, (history + prompt).strip(), model=model,
                             directory=workspace_path, agent=agent, agent_parts=agent_parts)
    return new_oc


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
        # subscribe_events(directory=...) is scoped to this session's workspace;
        # we still filter by session id below as a guard.
        # Track the latest snapshot of each assistant part (text + tool) keyed by
        # part id, preserving arrival order, so the persisted message matches the
        # live stream — including rendered tool results (e.g. query_collection).
        state = new_state()
        try:
            for evt in client.subscribe_events(directory=sess[4]):
                etype = evt.get('event', '')
                props = (evt.get('data') or {}).get('properties') or {}

                ev_sid = event_session_id(props)
                if ev_sid and ev_sid != opencode_session_id:
                    continue

                if apply_event(state, evt, opencode_session_id) == 'idle':
                    # Only persist when we captured the turn's message id, so the
                    # row id is deterministic and dedups with the background
                    # listener. Without it, defer to the listener (the
                    # authoritative writer) to avoid a duplicate random-id row.
                    if state['turn_msg_id']:
                        persist_turn(sid, state)
                    state = new_state()

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
    safe_name = safe_filename(f.filename)  # preserves Unicode (e.g. 中文) names
    rel = f"uploads/{safe_name}"
    dest = safe_resolve(workspace_path, rel)  # raises on traversal
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    f.save(dest)
    return jsonify({'name': safe_name, 'path': rel, 'size': os.path.getsize(dest)}), 201


@ai_chat_bp.route('/sessions/<sid>/skills', methods=['POST'])
@write_required
def upload_skill(sid):
    """Install an OpenCode skill zip into <workspace>/.opencode/skills/<name>/.
    OpenCode then discovers it via GET /skill?directory=<workspace>; the chat's
    command palette picks it up after loadPaletteItems refresh."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'file required', 'code': 'BAD_FILE'}), 400
    try:
        res = extract_skill_zip(sess[4], f)
    except SkillUploadError as e:
        return jsonify({'error': e.message, 'code': e.code}), 400
    return jsonify(res), 201


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
    changes, truncated, ok = git_changes(sess[4])
    return jsonify({'changes': changes, 'truncated': truncated, 'ok': ok})


@ai_chat_bp.route('/sessions/<sid>/diff', methods=['GET'])
@login_required
def file_diff_endpoint(sid):
    """Return a single changed file's diff (modified) or capped content (added).
    Path is validated against the workspace root to block traversal."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    rel = request.args.get('path', '').strip()
    if not rel:
        return jsonify({'error': 'path required', 'code': 'PATH_REQUIRED'}), 400
    try:
        safe_resolve(sess[4], rel)  # raises on traversal; result unused
    except Exception:
        return jsonify({'error': 'bad path', 'code': 'BAD_PATH'}), 400
    return jsonify(file_diff(sess[4], rel))


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
        resp = requests.get(f"{MCP_SERVER_URL}/tools", timeout=5)
        resp.raise_for_status()
        our_tools = resp.json()
        if not isinstance(our_tools, list):
            our_tools = []
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


@ai_chat_bp.route('/sessions/<sid>/commands', methods=['GET'])
@login_required
def list_session_commands(sid):
    """List OpenCode commands + skills for the chat's command palette."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    client = OpenCodeClient(OPENCODE_BASE_URL)
    try:
        commands = [{'name': c['name'], 'description': c.get('description', '')}
                    for c in client.list_commands(sess[4])]
    except Exception:
        commands = []
    try:
        skills = [{'name': s['name'], 'description': s.get('description', '')}
                  for s in client.list_skills(sess[4])]
    except Exception:
        skills = []
    return jsonify({'commands': commands, 'skills': skills})


@ai_chat_bp.route('/sessions/<sid>/command', methods=['POST'])
@write_required
def run_session_command(sid):
    """Run an OpenCode command in the session; its turn streams via the SSE proxy."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    body = request.get_json(force=True)
    command = (body.get('command') or '').strip()
    arguments = (body.get('arguments') or '').strip()
    if not command:
        return jsonify({'error': 'command required', 'code': 'COMMAND_REQUIRED'}), 400
    shown = '/' + command + (' ' + arguments if arguments else '')
    msg_id = 'msg_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'user', %s)",
            (msg_id, sid, json.dumps([{'type': 'text', 'text': shown}])),
        )
    OpenCodeClient(OPENCODE_BASE_URL).run_command(
        sess[2], command, arguments, model=OPENCODE_MODEL, directory=sess[4],
    )
    ensure_listener(sid, sess[2], sess[4])
    return jsonify({'messageId': msg_id}), 202


@ai_chat_bp.route('/sessions/<sid>/abort', methods=['POST'])
@write_required
def abort_session(sid):
    """Abort the in-flight turn (prompt or command). OpenCode then emits
    session.idle on the SSE so the UI clears its 'thinking' state."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    OpenCodeClient(OPENCODE_BASE_URL).abort_session(sess[2], directory=sess[4])
    return jsonify({'ok': True}), 200


@ai_chat_bp.route('/sessions/<sid>/messages/<msg_id>', methods=['DELETE'])
@write_required
def delete_message_onwards(sid, msg_id):
    """Remove a message and every message after it in the session — used by the
    user-bubble Edit / Retry affordances. Also aborts any in-flight turn so the
    next prompt isn't stacked on top of a still-streaming one (best-effort)."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT created_at FROM ai_chat_messages WHERE id = %s AND session_id = %s",
            (msg_id, sid),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'message not found', 'code': 'MESSAGE_NOT_FOUND'}), 404
        cur.execute(
            "DELETE FROM ai_chat_messages "
            "WHERE session_id = %s AND created_at >= %s",
            (sid, row[0]),
        )
        deleted = cur.rowcount
    try:
        OpenCodeClient(OPENCODE_BASE_URL).abort_session(sess[2], directory=sess[4])
    except Exception:
        pass  # best-effort: a non-streaming session returns 4xx and that's fine
    return jsonify({'deleted': deleted}), 200


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


@ai_chat_bp.route('/sessions/<sid>/close', methods=['POST'])
@write_required
def close_session(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    if sess[3] in ('archived', 'deleted'):
        return jsonify({'error': '该状态会话不可关闭', 'code': 'INVALID_STATUS'}), 409
    # close 是软关闭、可 reopen：仅改 status + 停 listener；
    # 保留 token / workspace / OpenCode session，使 reopen 能续上（失效则 M3 重建）。
    stop_listener(sid)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET status='closed' WHERE id=%s AND user_id=%s",
                    (sid, user['userId']))
    log_operation('update', 'ai_chat_session', sid, sid, '关闭会话')
    return jsonify({'ok': True, 'status': 'closed'})


@ai_chat_bp.route('/sessions/<sid>/reopen', methods=['POST'])
@write_required
def reopen_session(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    if sess[3] == 'archived':
        return jsonify({'error': '已归档会话不可重开', 'code': 'SESSION_ARCHIVED'}), 403
    if sess[3] == 'deleted':
        return jsonify({'error': '已删除会话不可重开', 'code': 'INVALID_STATUS'}), 409
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET status = 'active' WHERE id=%s AND user_id=%s",
                    (sid, user['userId']))
    log_operation('update', 'ai_chat_session', sid, sid, '重开会话')
    return jsonify({'ok': True, 'status': 'active'})


# --- Admin governance endpoints (require admin.ai_chat_admin capability) ---


@ai_chat_bp.route('/admin/sessions', methods=['GET'])
@require_permission('admin.ai_chat_admin')
def admin_list_sessions():
    """List all sessions across all users (admin only, max 500)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, title, status, last_active_at FROM ai_chat_sessions "
            "WHERE batch_id IS NULL ORDER BY last_active_at DESC NULLS LAST, id DESC LIMIT 500")
        rows = cur.fetchall()
    return jsonify({'sessions': [
        {'id': r[0], 'userId': r[1], 'title': r[2] or '新会话', 'status': r[3],
         'lastActiveAt': r[4].isoformat() if r[4] else None} for r in rows]})


@ai_chat_bp.route('/sessions/<sid>/archive', methods=['POST'])
@require_permission('admin.ai_chat_admin')
def archive_session(sid):
    """Archive any session (admin only)."""
    stop_listener(sid)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET status='archived' WHERE id=%s", (sid,))
        if cur.rowcount == 0:
            return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    log_operation('update', 'ai_chat_session', sid, sid, '归档会话（admin）')
    return jsonify({'ok': True, 'status': 'archived'})


