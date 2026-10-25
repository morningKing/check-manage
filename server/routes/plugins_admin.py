"""第一方插件管理 API（plugin-architecture spec §4）。"""
from flask import Blueprint, jsonify, g, request

from auth import login_required, require_permission
from db import get_db
from utils import plugin_registry
from utils.operation_log import log_operation

plugins_bp = Blueprint('plugins', __name__, url_prefix='/plugins')


@plugins_bp.route('', methods=['GET'])
@login_required
def list_plugins():
    return jsonify([{
        'id': m.id, 'name': m.name, 'description': m.description,
        'version': m.version, 'enabled': plugin_registry.plugin_enabled(m.id),
    } for m in plugin_registry.PLUGINS.values()])


@plugins_bp.route('/<plugin_id>', methods=['PUT'])
@require_permission('admin.plugins')
def set_plugin_enabled(plugin_id):
    m = plugin_registry.PLUGINS.get(plugin_id)
    if not m:
        return jsonify({'error': '插件不存在'}), 404
    body = request.get_json(silent=True) or {}
    enabled = body.get('enabled')
    if not isinstance(enabled, bool):
        return jsonify({'error': 'enabled 必须为布尔值'}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO platform_plugins (plugin_id, enabled, updated_at, updated_by)"
                " VALUES (%s, %s, NOW(), %s)"
                " ON CONFLICT (plugin_id) DO UPDATE SET enabled = EXCLUDED.enabled,"
                " updated_at = NOW(), updated_by = EXCLUDED.updated_by",
                (plugin_id, enabled, g.current_user.get('userId')))
        conn.commit()
    plugin_registry.invalidate(plugin_id)
    log_operation('plugin.update', 'platform_plugin', plugin_id, m.name,
                  f'{"启用" if enabled else "禁用"}插件 {m.name}')
    return jsonify({'id': plugin_id, 'enabled': enabled})
