import jwt
import hashlib
import datetime
from functools import wraps
from flask import request, jsonify, g
from config import JWT_SECRET, JWT_EXPIRY_HOURS
from db import get_db


def create_token(user):
    """Create JWT token with user id, username, and role in payload."""
    payload = {
        'userId': user['id'],
        'username': user['username'],
        'role': user['role'],
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def decode_token(token):
    """Decode and validate JWT token. Returns payload dict or None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def login_required(f):
    """Decorator: require valid JWT in Authorization header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': '未登录'}), 401
        token = auth_header.split(' ', 1)[1]
        payload = decode_token(token)
        if not payload:
            return jsonify({'error': '登录已过期'}), 401
        g.current_user = payload
        return f(*args, **kwargs)
    return decorated


def write_required(f):
    """Decorator: require non-guest role (implies login_required). Guest users get 403."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': '未登录'}), 401
        token = auth_header.split(' ', 1)[1]
        payload = decode_token(token)
        if not payload:
            return jsonify({'error': '登录已过期'}), 401
        if payload.get('role') == 'guest':
            return jsonify({'error': '访客无操作权限'}), 403
        g.current_user = payload
        return f(*args, **kwargs)
    return decorated


def login_required_sse(f):
    """Like login_required, but also accepts the JWT via an ?access_token=
    query param. The browser EventSource API cannot set request headers, so
    SSE endpoints fall back to the query param.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1]
        else:
            token = request.args.get('access_token', '')
        if not token:
            return jsonify({'error': '未登录'}), 401
        payload = decode_token(token)
        if not payload:
            return jsonify({'error': '登录已过期'}), 401
        g.current_user = payload
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator: require admin role (implies login_required)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': '未登录'}), 401
        token = auth_header.split(' ', 1)[1]
        payload = decode_token(token)
        if not payload:
            return jsonify({'error': '登录已过期'}), 401
        if payload.get('role') != 'admin':
            return jsonify({'error': '权限不足'}), 403
        g.current_user = payload
        return f(*args, **kwargs)
    return decorated


def _user_has_permission(payload, permission_key):
    """能力门：超管（roles.is_superuser=True）放行、否则靠 admin_keys 命中判定。

    本 helper 给 require_permission 与 require_permission_sse 共用。两装饰
    器只在 token 提取方式上不同（header only / header or query），能力判定
    收敛到此唯一路径——逻辑漂移是安全事故常见来源。超管放行靠 DB 的
    roles.is_superuser（经 utils.permissions.can_admin），不在 JWT role slug
    上做任何短路，保留与重构前完全一致的判定语义。
    """
    from utils.permissions import can_admin
    return can_admin(payload.get('role'), permission_key)


def require_permission(permission_key):
    """Decorator: require the current user's role to hold `permission_key`
    (superuser bypasses). Implies login_required.
    """
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                return jsonify({'error': '未登录'}), 401
            token = auth_header.split(' ', 1)[1]
            payload = decode_token(token)
            if not payload:
                return jsonify({'error': '登录已过期'}), 401
            if not _user_has_permission(payload, permission_key):
                return jsonify({'error': '权限不足'}), 403
            g.current_user = payload
            return f(*args, **kwargs)
        return decorated
    return wrapper


def require_permission_sse(permission_key):
    """Like require_permission, but also accepts the JWT via ?access_token=
    (browser <a download> / EventSource can't set Authorization header).

    能力判定与 require_permission 完全同源——都通过 _user_has_permission
    走 utils.permissions.can_admin，两份实现一锁定就同步锁定，避免一处改
    了 header-only 装饰器忘了改 SSE 装饰器的关系人权限事故。
    """
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ', 1)[1]
            else:
                token = request.args.get('access_token', '')
            if not token:
                return jsonify({'error': '未登录'}), 401
            payload = decode_token(token)
            if not payload:
                return jsonify({'error': '登录已过期'}), 401
            g.current_user = payload
            if not _user_has_permission(payload, permission_key):
                return jsonify({'error': '权限不足'}), 403
            return f(*args, **kwargs)
        return decorated
    return wrapper


def hash_api_key(key):
    """Hash an API key using SHA-256."""
    return hashlib.sha256(key.encode()).hexdigest()


def api_key_required(f):
    """Decorator: require valid API key in X-API-Key header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key', '')
        if not api_key:
            return jsonify({'error': 'Missing API key'}), 401

        key_hash = hash_api_key(api_key)

        with get_db() as conn:
            cur = conn.cursor()
            # LEFT JOIN (not JOIN): stock keys predating owner_user_id, or a
            # deleted owner, leave owner_user_id/ownerUsername/ownerRole all
            # NULL — require_bound_key already rejects those, this just keeps
            # the SELECT from filtering the key row itself out.
            cur.execute(
                'SELECT k.id, k.name, k.is_active, k.owner_user_id, u.username, u.role '
                'FROM api_keys k LEFT JOIN users u ON u.id = k.owner_user_id '
                'WHERE k.key_hash = %s',
                (key_hash,),
            )
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'Invalid API key'}), 401
            if not row[2]:
                return jsonify({'error': 'API key has been revoked'}), 401

            cur.execute(
                'UPDATE api_keys SET last_used_at = NOW() WHERE id = %s',
                (row[0],),
            )

        g.api_key_info = {
            'id': row[0], 'name': row[1], 'ownerUserId': row[3],
            'ownerUsername': row[4], 'ownerRole': row[5],
        }
        return f(*args, **kwargs)
    return decorated


def require_bound_key(f):
    """挡住未绑定用户的密钥（用在 api_key_required 之后的对外端点上）。

    存量密钥的 owner_user_id 是 NULL。放行它们会导致外部调用以某个人的名义
    跑 AI、烧他的额度、把任务/数据塞进他的账号下 —— 所以一律拒绝，要求重建密钥。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        info = getattr(g, 'api_key_info', {}) or {}
        if not info.get('ownerUserId'):
            return jsonify({'error': '该密钥未绑定用户，请在密钥管理中重新创建'}), 403
        return f(*args, **kwargs)
    return decorated
