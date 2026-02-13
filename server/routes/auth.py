from flask import Blueprint, request, jsonify, g
from werkzeug.security import check_password_hash, generate_password_hash
from db import get_db
from auth import create_token, login_required
from utils.operation_log import log_operation

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/auth/login', methods=['POST'])
def login():
    """Authenticate user and return JWT token."""
    body = request.get_json(force=True)
    username = body.get('username', '').strip()
    password = body.get('password', '')

    if not username or not password:
        return jsonify({'error': '请输入用户名和密码'}), 400

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, username, password_hash, display_name, role FROM users WHERE username = %s',
            (username,),
        )
        row = cur.fetchone()

    if not row or not check_password_hash(row[2], password):
        return jsonify({'error': '用户名或密码错误'}), 401

    user = {
        'id': row[0],
        'username': row[1],
        'displayName': row[3],
        'role': row[4],
    }
    token = create_token(user)
    return jsonify({'token': token, 'user': user})


@auth_bp.route('/auth/me', methods=['GET'])
@login_required
def get_current_user():
    """Return current user info from token."""
    payload = g.current_user
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, username, display_name, role FROM users WHERE id = %s',
            (payload['userId'],),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': '用户不存在'}), 404
    return jsonify({
        'id': row[0],
        'username': row[1],
        'displayName': row[2],
        'role': row[3],
    })


@auth_bp.route('/auth/password', methods=['PUT'])
@login_required
def change_password():
    """Allow current user to change their own password."""
    body = request.get_json(force=True)
    old_password = body.get('oldPassword', '')
    new_password = body.get('newPassword', '')

    if not old_password or not new_password:
        return jsonify({'error': '请输入旧密码和新密码'}), 400
    if len(new_password) < 6:
        return jsonify({'error': '新密码不能少于6个字符'}), 400

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT password_hash FROM users WHERE id = %s', (g.current_user['userId'],))
        row = cur.fetchone()
        if not row or not check_password_hash(row[0], old_password):
            return jsonify({'error': '旧密码不正确'}), 400
        cur.execute(
            'UPDATE users SET password_hash = %s WHERE id = %s',
            (generate_password_hash(new_password), g.current_user['userId']),
        )
    log_operation('update', 'user', g.current_user['userId'], g.current_user['username'], '修改密码')
    return jsonify({'message': '密码修改成功'})
