"""系统配置 API 路由

GET  /system-config — 获取系统配置（所有角色可读）
PUT  /system-config — 更新系统配置（仅管理员）
"""

from flask import Blueprint, request, jsonify, g
import psycopg2.extras
from db import get_db
from auth import login_required, admin_required

system_config_bp = Blueprint('system_config', __name__, url_prefix='/system-config')


@system_config_bp.route('', methods=['GET'])
@login_required
def get_system_config():
    """获取系统配置"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute('SELECT system_name, system_short_name, logo_url FROM system_config WHERE id = 1')
        row = cur.fetchone()

    if not row:
        return jsonify({'error': '系统配置不存在'}), 404

    return jsonify({
        'systemName': row['system_name'],
        'systemShortName': row['system_short_name'],
        'logoUrl': row['logo_url']
    })


@system_config_bp.route('', methods=['PUT'])
@admin_required
def update_system_config():
    """更新系统配置（仅管理员）"""
    body = request.get_json(force=True)

    system_name = body.get('systemName', '').strip()
    system_short_name = body.get('systemShortName', '').strip()
    logo_url = body.get('logoUrl')

    if not system_name:
        return jsonify({'error': '系统名称不能为空'}), 400
    if not system_short_name:
        return jsonify({'error': '系统简称不能为空'}), 400

    # 获取当前用户名作为更新人
    updated_by = g.current_user.get('username', '')

    with get_db() as conn:
        cur = conn.cursor()
        # 检查系统配置是否存在
        cur.execute('SELECT id FROM system_config WHERE id = 1')
        if not cur.fetchone():
            return jsonify({'error': '系统配置不存在'}), 404

        cur.execute("""
            UPDATE system_config
            SET system_name = %s, system_short_name = %s, logo_url = %s, updated_at = NOW(), updated_by = %s
            WHERE id = 1
        """, (system_name, system_short_name, logo_url, updated_by))
        conn.commit()

    return jsonify({
        'systemName': system_name,
        'systemShortName': system_short_name,
        'logoUrl': logo_url
    })