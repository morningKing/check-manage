from flask import Blueprint, request, jsonify, g
from werkzeug.security import generate_password_hash
from db import get_db
from auth import admin_required
from datetime import timezone
from utils.operation_log import log_operation
import uuid

users_bp = Blueprint('users', __name__)


def _role_exists(cur, role_id):
    cur.execute('SELECT 1 FROM roles WHERE id = %s', (role_id,))
    return cur.fetchone() is not None


def format_ts(dt):
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def row_to_dict(row):
    return {
        'id': row[0],
        'username': row[1],
        'displayName': row[2],
        'role': row[3],
        'createdAt': format_ts(row[4]),
    }


@users_bp.route('/users', methods=['GET'])
@admin_required
def list_users():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, username, display_name, role, created_at FROM users ORDER BY created_at')
        rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@users_bp.route('/users', methods=['POST'])
@admin_required
def create_user():
    body = request.get_json(force=True)
    username = body.get('username', '').strip()
    password = body.get('password', '')
    display_name = body.get('displayName', '').strip()
    role = body.get('role', 'guest')

    if not username or not password or not display_name:
        return jsonify({'error': '用户名、密码和显示名称不能为空'}), 400
    if len(password) < 6:
        return jsonify({'error': '密码不能少于6个字符'}), 400

    user_id = f'user-{uuid.uuid4().hex[:8]}'
    with get_db() as conn:
        cur = conn.cursor()
        if not _role_exists(cur, role):
            return jsonify({'error': '无效的角色'}), 400
        cur.execute('SELECT id FROM users WHERE username = %s', (username,))
        if cur.fetchone():
            return jsonify({'error': '用户名已存在'}), 409
        cur.execute(
            'INSERT INTO users (id, username, password_hash, display_name, role) VALUES (%s,%s,%s,%s,%s)',
            (user_id, username, generate_password_hash(password), display_name, role),
        )
        cur.execute('SELECT id, username, display_name, role, created_at FROM users WHERE id = %s', (user_id,))
        row = cur.fetchone()
    log_operation('create', 'user', user_id, username,
                  f'新增用户「{display_name}」，角色：{role}')
    return jsonify(row_to_dict(row)), 201


@users_bp.route('/users/<user_id>', methods=['PUT'])
@admin_required
def update_user(user_id):
    body = request.get_json(force=True)
    with get_db() as conn:
        cur = conn.cursor()
        sets = []
        params = []
        if 'displayName' in body:
            sets.append('display_name = %s')
            params.append(body['displayName'])
        if 'role' in body:
            if not _role_exists(cur, body['role']):
                return jsonify({'error': '无效的角色'}), 400
            sets.append('role = %s')
            params.append(body['role'])
        if 'password' in body and body['password']:
            if len(body['password']) < 6:
                return jsonify({'error': '密码不能少于6个字符'}), 400
            sets.append('password_hash = %s')
            params.append(generate_password_hash(body['password']))
        if not sets:
            return jsonify({'error': '没有要更新的字段'}), 400
        params.append(user_id)
        cur.execute(f'UPDATE users SET {", ".join(sets)} WHERE id = %s', params)
        cur.execute('SELECT id, username, display_name, role, created_at FROM users WHERE id = %s', (user_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({'error': '用户不存在'}), 404
    log_operation('update', 'user', user_id, row_to_dict(row)['username'],
                  f'修改用户「{row_to_dict(row)["username"]}」')
    return jsonify(row_to_dict(row))


@users_bp.route('/users/<user_id>', methods=['DELETE'])
@admin_required
def delete_user(user_id):
    if g.current_user['userId'] == user_id:
        return jsonify({'error': '不能删除自己的账号'}), 400
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT username, display_name FROM users WHERE id = %s', (user_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '用户不存在'}), 404
        username, display_name = row[0], row[1]
        cur.execute('DELETE FROM users WHERE id = %s', (user_id,))
    log_operation('delete', 'user', user_id, username,
                  f'删除用户「{display_name}」（{username}）')
    return jsonify({})
