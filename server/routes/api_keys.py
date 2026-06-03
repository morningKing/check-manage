from flask import Blueprint, request, jsonify
from db import get_db
from datetime import timezone
from auth import hash_api_key, require_permission
from utils.operation_log import log_operation
import uuid
import secrets

api_keys_bp = Blueprint('api_keys', __name__)


def format_ts(dt):
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def row_to_dict(row):
    return {
        'id': row[0],
        'name': row[1],
        'createdAt': format_ts(row[2]),
        'lastUsedAt': format_ts(row[3]),
        'isActive': row[4],
    }


@api_keys_bp.route('/apiKeys', methods=['GET'])
@require_permission('admin.api_keys')
def list_api_keys():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, created_at, last_used_at, is_active '
            'FROM api_keys ORDER BY created_at DESC'
        )
        rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@api_keys_bp.route('/apiKeys', methods=['POST'])
@require_permission('admin.api_keys')
def create_api_key():
    body = request.get_json(force=True)
    name = body.get('name', '').strip()
    if not name:
        return jsonify({'error': '请输入密钥名称'}), 400

    key_id = f'ak-{uuid.uuid4().hex[:8]}'
    raw_key = f'cm_{secrets.token_urlsafe(32)}'
    key_hash_val = hash_api_key(raw_key)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO api_keys (id, name, key_hash) VALUES (%s, %s, %s)',
            (key_id, name, key_hash_val),
        )
        cur.execute(
            'SELECT id, name, created_at, last_used_at, is_active '
            'FROM api_keys WHERE id = %s',
            (key_id,),
        )
        row = cur.fetchone()

    log_operation('create', 'api_key', key_id, name, f'创建API密钥「{name}」')

    result = row_to_dict(row)
    result['key'] = raw_key
    return jsonify(result), 201


@api_keys_bp.route('/apiKeys/<key_id>', methods=['PUT'])
@require_permission('admin.api_keys')
def toggle_api_key(key_id):
    body = request.get_json(force=True)
    is_active = body.get('isActive')
    if is_active is None:
        return jsonify({'error': '缺少参数'}), 400

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE api_keys SET is_active = %s WHERE id = %s',
            (is_active, key_id),
        )
        cur.execute(
            'SELECT id, name, created_at, last_used_at, is_active '
            'FROM api_keys WHERE id = %s',
            (key_id,),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    result = row_to_dict(row)
    action_desc = '启用' if is_active else '停用'
    log_operation('update', 'api_key', key_id, result['name'],
                  f'{action_desc}API密钥「{result["name"]}」')
    return jsonify(result)


@api_keys_bp.route('/apiKeys/<key_id>', methods=['DELETE'])
@require_permission('admin.api_keys')
def delete_api_key(key_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name FROM api_keys WHERE id = %s', (key_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        name = row[0]
        cur.execute('DELETE FROM api_keys WHERE id = %s', (key_id,))
    log_operation('delete', 'api_key', key_id, name, f'删除API密钥「{name}」')
    return jsonify({})
