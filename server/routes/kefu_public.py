"""公开匿名客服入口（无 JWT，X-Visitor-Id 识别 + 限速）。
公开攻击面收敛于此蓝图，便于审计加固。"""
import json
import logging
import os
import secrets
import threading
from flask import Blueprint, request, jsonify, Response, stream_with_context
from db import get_db
from utils import kefu_repo
from utils.rate_limit import RateLimiter
from utils.opencode_client import OpenCodeClient
from utils.chat_persist import (
    ensure_listener, new_state, apply_event, persist_turn,
    event_session_id, has_listener,
)
from utils.workspace import safe_resolve, WorkspacePathError
from utils.filename import safe_filename
from config import OPENCODE_BASE_URL, OPENCODE_MODEL

logger = logging.getLogger(__name__)

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_ALLOWED_EXT = {'.txt', '.md', '.csv', '.json', '.pdf', '.png', '.jpg',
                '.jpeg', '.gif', '.xlsx', '.docx'}

# Rate-limit defaults applied when the instance config omits a key entirely.
# An explicit 0 in config still means "unlimited" (admin opt-out).
DEFAULT_PER_MINUTE = 30
DEFAULT_PER_DAY = 500

# Fixed IP-only rate floors — always enforced, not subject to the explicit-0 opt-out.
DEFAULT_IP_PER_MINUTE = 60
DEFAULT_IP_PER_DAY = 1000

# SSE concurrency caps.
MAX_SSE_PER_VISITOR = 3   # max open streams per (instance, visitor) pair
MAX_SSE_PER_IP = 6        # max open streams per client IP (across all visitors)
_sse_active: dict = {}   # key -> count
_sse_lock = threading.Lock()


def _format_sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

kefu_public_bp = Blueprint('kefu_public', __name__, url_prefix='/kefu')
_limiter = RateLimiter()


def _visitor_id():
    return (request.headers.get('X-Visitor-Id') or '').strip()


def _client_ip() -> str:
    # Trust the bundled reverse proxy to set X-Forwarded-For correctly.
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.remote_addr


def _public_config(inst: dict) -> dict:
    return {
        'slug': inst['slug'], 'name': inst['name'],
        'welcome_message': inst.get('welcome_message'),
        'guided_questions': inst.get('guided_questions') or [],
        'branding': inst.get('branding') or {},
        'enabled': inst.get('enabled', True),
    }


def _rate_ok(inst, vid):
    rl = inst.get('rate_limit') or {}
    key = f"{inst['id']}:{vid}:{_client_ip()}"
    # Absent key → safe default floor; explicit 0 → unlimited (admin opt-out).
    pm = rl.get('perMinute')
    pm = DEFAULT_PER_MINUTE if pm is None else int(pm)
    pd = rl.get('perDay')
    pd = DEFAULT_PER_DAY if pd is None else int(pd)
    if not _limiter.allow(key, pm, pd):
        return False
    # Second IP-only bucket with fixed floors (no unlimited opt-out for this bucket).
    ip_key = f"ip:{inst['id']}:{_client_ip()}"
    return _limiter.allow(ip_key, DEFAULT_IP_PER_MINUTE, DEFAULT_IP_PER_DAY)


def _sse_acquire(key: str) -> bool:
    """Atomically increment the active-stream counter for *key*.

    Returns True and bumps the count if still below MAX_SSE_PER_VISITOR;
    returns False (without modifying the count) when the cap is already met.
    Unit-testable without a Flask request context.
    """
    with _sse_lock:
        count = _sse_active.get(key, 0)
        if count >= MAX_SSE_PER_VISITOR:
            return False
        _sse_active[key] = count + 1
        return True


def _sse_release(key: str) -> None:
    """Decrement the active-stream counter for *key*, removing it when zero."""
    with _sse_lock:
        count = _sse_active.get(key, 0)
        if count <= 1:
            _sse_active.pop(key, None)
        else:
            _sse_active[key] = count - 1


def _sse_acquire_ip(key: str) -> bool:
    """Atomically increment the active-stream counter for IP-keyed *key*.

    Returns True and bumps the count if still below MAX_SSE_PER_IP;
    returns False (without modifying the count) when the cap is already met.
    Unit-testable without a Flask request context.
    """
    with _sse_lock:
        count = _sse_active.get(key, 0)
        if count >= MAX_SSE_PER_IP:
            return False
        _sse_active[key] = count + 1
        return True


def _sse_release_ip(key: str) -> None:
    """Decrement the active-stream counter for IP-keyed *key*, removing it when zero."""
    with _sse_lock:
        count = _sse_active.get(key, 0)
        if count <= 1:
            _sse_active.pop(key, None)
        else:
            _sse_active[key] = count - 1


@kefu_public_bp.route('/i/<slug>', methods=['GET'])
def public_config(slug):
    inst = kefu_repo.get_instance_by_slug(slug)
    if not inst:
        return jsonify({'error': 'not found'}), 404
    return jsonify(_public_config(inst))


@kefu_public_bp.route('/i/<slug>/sessions', methods=['POST'])
def create_session(slug):
    vid = _visitor_id()
    if not vid:
        return jsonify({'error': 'X-Visitor-Id required'}), 400
    inst = kefu_repo.get_instance_by_slug(slug)
    if not inst:
        return jsonify({'error': 'not found'}), 404
    if not inst.get('enabled', True):
        return jsonify({'error': '客服暂时下线'}), 403
    if not _rate_ok(inst, vid):
        return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
    out = kefu_repo.create_kefu_session(inst, vid)
    return jsonify(out), 201


@kefu_public_bp.route('/sessions/<sid>/messages', methods=['GET'])
def history(sid):
    vid = _visitor_id()
    sess = kefu_repo.load_kefu_session(sid, vid)
    if not sess:
        return jsonify({'error': 'session not found'}), 404
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, role, content, created_at FROM ai_chat_messages "
            "WHERE session_id=%s ORDER BY created_at ASC LIMIT 500", (sid,))
        rows = cur.fetchall()
    return jsonify({'messages': [
        {'id': r[0], 'role': r[1], 'content': r[2],
         'createdAt': r[3].isoformat() if r[3] else None}
        for r in rows]})


@kefu_public_bp.route('/sessions/<sid>/messages', methods=['POST'])
def send_message(sid):
    vid = _visitor_id()
    sess = kefu_repo.load_kefu_session(sid, vid)
    if not sess:
        return jsonify({'error': 'session not found'}), 404
    inst = kefu_repo.get_instance(sess[5])
    if not inst:
        return jsonify({'error': 'instance not found'}), 404
    if not _rate_ok(inst, vid):
        return jsonify({'error': '请求过于频繁，请稍后再试'}), 429

    body = request.get_json(force=True, silent=True) or {}
    content = (body.get('content') or '').strip()
    attachments = body.get('attachments') or []
    if not content and not attachments:
        return jsonify({'error': 'content required'}), 400

    workspace_path = sess[4]
    stored_parts = [{'type': 'text', 'text': content}] if content else []
    # 护栏与人设已注入 AGENTS.md，这里只发用户内容 + 附件路径提示
    prompt = content
    for rel in attachments:
        try:
            safe_resolve(sess[4], rel)   # raises WorkspacePathError on traversal/abs path
        except WorkspacePathError:
            return jsonify({'error': f'invalid attachment path: {rel}'}), 400
        name = os.path.basename(rel)
        stored_parts.append({'type': 'file', 'name': name, 'path': rel})
        prompt += f"\n\n[用户上传的文件 {name}，路径：{rel}（可用工具读取）]"

    msg_id = 'msg_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s,%s,'user',%s)",
            (msg_id, sid, json.dumps(stored_parts or [{'type': 'text', 'text': ''}])))

    client = OpenCodeClient(OPENCODE_BASE_URL)
    oc_sid = sess[2]
    model = inst.get('model') or OPENCODE_MODEL
    agent = inst.get('agent') or ''
    client.send_prompt_async(oc_sid, prompt.strip(), model=model,
                             directory=workspace_path, agent=agent, agent_parts=[])
    ensure_listener(sid, oc_sid, workspace_path)
    return jsonify({'messageId': msg_id}), 202


@kefu_public_bp.route('/sessions/<sid>/events', methods=['GET'])
def events(sid):
    vid = (request.args.get('visitor_id') or '').strip()
    sess = kefu_repo.load_kefu_session(sid, vid)
    if not sess:
        return jsonify({'error': 'session not found'}), 404

    sse_key = f"{sess[5]}:{vid}"
    ip_key = f"ip:{_client_ip()}"

    if not _sse_acquire(sse_key):
        return jsonify({'error': '并发连接过多，请稍后再试'}), 429
    # All-or-nothing: if IP cap is full, release the visitor slot we just acquired.
    if not _sse_acquire_ip(ip_key):
        _sse_release(sse_key)
        return jsonify({'error': '并发连接过多，请稍后再试'}), 429

    try:
        oc_sid = sess[2]
        client = OpenCodeClient(OPENCODE_BASE_URL)

        def generate():
            logger.info('kefu sse open session=%s oc=%s', sid, oc_sid)
            state = new_state()
            try:
                for evt in client.subscribe_events(directory=sess[4]):
                    etype = evt.get('event', '')
                    props = (evt.get('data') or {}).get('properties') or {}
                    ev_sid = event_session_id(props)
                    if ev_sid and ev_sid != oc_sid:
                        continue
                    if apply_event(state, evt, oc_sid) == 'idle':
                        if state['turn_msg_id'] and not has_listener(sid):
                            persist_turn(sid, state)
                        state = new_state()
                    yield _format_sse(etype, props)
            except GeneratorExit:
                return
            except Exception:
                logger.exception('kefu sse stream error session=%s oc=%s', sid, oc_sid)
                return
            finally:
                _sse_release(sse_key)
                _sse_release_ip(ip_key)
                logger.info('kefu sse closed session=%s', sid)

        return Response(stream_with_context(generate()), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    except Exception:
        _sse_release(sse_key)
        _sse_release_ip(ip_key)
        raise


@kefu_public_bp.route('/sessions/<sid>/files', methods=['POST'])
def upload(sid):
    vid = _visitor_id()
    sess = kefu_repo.load_kefu_session(sid, vid)
    if not sess:
        return jsonify({'error': 'session not found'}), 404
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'file required'}), 400
    # Size check before type check so an oversize file always returns 413
    stream = f.stream
    stream.seek(0, os.SEEK_END)
    if stream.tell() > _MAX_UPLOAD_BYTES:
        return jsonify({'error': '文件超过 20MB 上限'}), 413
    stream.seek(0)
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in _ALLOWED_EXT:
        return jsonify({'error': f'不支持的文件类型 {ext}'}), 415
    safe_name = safe_filename(f.filename)
    rel = f"uploads/{safe_name}"
    try:
        dest = safe_resolve(sess[4], rel)
    except WorkspacePathError:
        return jsonify({'error': 'bad path'}), 400
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    f.save(dest)
    return jsonify({'name': safe_name, 'path': rel, 'size': os.path.getsize(dest)}), 201
