"""批任务会话人工接管公开入口（无 JWT，接管令牌 + 双层限速）。
公开攻击面收敛于此蓝图（与 kefu_public 同纪律）：全系统未挂 JWT 的路由仅
/auth/login、/kefu/*、/external/takeover/* 三处，评审时按此清单核对。"""
import logging

from flask import Blueprint, jsonify, request, g

from db import get_db
from utils import batch_repo, takeover_repo
from utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 20000
SEND_PER_MINUTE, SEND_PER_DAY = 10, 200        # 令牌桶
IP_PER_MINUTE, IP_PER_DAY = 60, 1000           # IP 桶（固定地板，对齐 kefu）

external_takeover_bp = Blueprint('external_takeover', __name__,
                                 url_prefix='/external/takeover')
_limiter = RateLimiter()

_INVALID = ({'error': '链接无效或已过期'}, 404)


def _client_ip() -> str:
    xff = request.headers.get('X-Forwarded-For', '')
    return xff.split(',')[0].strip() if xff else (request.remote_addr or '')


@external_takeover_bp.after_request
def _no_store(resp):
    resp.headers.setdefault('Cache-Control', 'no-store')
    return resp


def _log_takeover(action: str, target_id: str, target_name: str, description: str):
    """公开蓝图无 g.current_user / g.api_key_info——以令牌属主身份落 oplog
    （operation_log._write_log_row 是显式身份入口；发消息路径频控 10/min，
    一次 users 查询可忽略）。"""
    r = getattr(g, 'takeover', None)
    if not r:
        return
    from utils.operation_log import _write_log_row
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username, role FROM users WHERE id = %s",
                        (r['owner_user_id'],))
            row = cur.fetchone()
    _write_log_row(r['owner_user_id'], row[0] if row else '',
                   (row[1] if row else '') or 'external-takeover',
                   action, 'ai_chat_session', target_id, target_name, description)


def _guard():
    """令牌校验 + 限速。失败时直接返回 (body, status)，成功返回 None 并写 g.takeover。"""
    row = takeover_repo.resolve_takeover_token(request.args.get('t'))
    if not row:
        return _INVALID
    key = f"tk:{row['token_id']}:{_client_ip()}"
    if not _limiter.allow(key, SEND_PER_MINUTE, SEND_PER_DAY):
        return {'error': '请求过于频繁，请稍后再试'}, 429
    if not _limiter.allow(f"tkip:{_client_ip()}", IP_PER_MINUTE, IP_PER_DAY):
        return {'error': '请求过于频繁，请稍后再试'}, 429
    g.takeover = row
    return None


def _child_status_fresh() -> str:
    """resolve 时的状态可能陈旧，发送前重读。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s AND batch_id=%s",
                        (g.takeover['session_id'], g.takeover['batch_id']))
            row = cur.fetchone()
    return row[0] if row else ''


@external_takeover_bp.get('/session')
def takeover_session_info():
    denied = _guard()
    if denied:
        return denied
    r = g.takeover
    return jsonify({'batchName': r['batch_name'], 'batchSeq': r['batch_seq'],
                    'inputFile': r['batch_input_file'], 'title': r['title'],
                    'childStatus': r['child_status'], 'batchStatus': r['batch_status'],
                    'expiresAt': r['expires_at'].isoformat()})


@external_takeover_bp.get('/session/messages')
def takeover_messages():
    denied = _guard()
    if denied:
        return denied
    r = g.takeover
    msgs = batch_repo.get_child_messages(r['session_id'])
    return jsonify({
        'childStatus': _child_status_fresh(),
        'messages': [
            {'id': m['id'], 'role': m['role'], 'content': m['content'],
             'createdAt': m['created_at'].isoformat() if m['created_at'] else None}
            for m in msgs['messages']],
        'total': msgs['total'], 'truncated': msgs['truncated'],
    })


@external_takeover_bp.post('/session/messages')
def takeover_send():
    denied = _guard()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    prompt = (body.get('prompt') or '').strip()
    if not prompt:
        return {'error': 'prompt required'}, 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return {'error': f'prompt 超过 {MAX_PROMPT_CHARS} 字符上限'}, 400
    status = _child_status_fresh()
    if status in ('pending', 'running'):
        return {'error': {'code': 'TASK_RUNNING',
                          'message': '任务执行中，请外部系统先暂停后再接管'}}, 409
    if status not in ('completed', 'failed', 'cancelled', 'needs_review', 'paused'):
        return _INVALID
    from utils.batch_engine import get_worker
    try:
        batch_repo.continue_child(g.takeover['owner_user_id'], g.takeover['batch_id'],
                                  g.takeover['session_id'], prompt,
                                  repark=(status == 'paused'))
    except ValueError as e:
        return {'error': {'code': 'STATE_CONFLICT', 'message': str(e)}}, 409
    get_worker().notify()
    _log_takeover('ai.batch_takeover_message', g.takeover['session_id'],
                  g.takeover['title'] or '接管会话',
                  f'接管对话 {len(prompt)} 字符（prev_status={status}）')
    return jsonify({'queued': True}), 202
