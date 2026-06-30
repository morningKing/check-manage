"""公开匿名客服入口（无 JWT，X-Visitor-Id 识别 + 限速）。
公开攻击面收敛于此蓝图，便于审计加固。"""
import json
from flask import Blueprint, request, jsonify
from db import get_db
from utils import kefu_repo
from utils.rate_limit import RateLimiter

kefu_public_bp = Blueprint('kefu_public', __name__, url_prefix='/kefu')
_limiter = RateLimiter()


def _visitor_id():
    return (request.headers.get('X-Visitor-Id') or '').strip()


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
    key = f"{inst['id']}:{vid}:{request.remote_addr}"
    return _limiter.allow(key, int(rl.get('perMinute') or 0), int(rl.get('perDay') or 0))


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
