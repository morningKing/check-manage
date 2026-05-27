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
            cur.execute(
                'SELECT id, name, is_active FROM api_keys WHERE key_hash = %s',
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

        g.api_key_info = {'id': row[0], 'name': row[1]}
        return f(*args, **kwargs)
    return decorated
