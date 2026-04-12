from flask import Blueprint, request, jsonify
from db import get_db
from datetime import datetime, timezone
from auth import login_required, admin_required
from utils.operation_log import log_operation
from utils.page_config_relations import get_page_config_relations
import psycopg2.extras

page_configs_bp = Blueprint('page_configs', __name__)


def format_ts(dt):
    """Format datetime to ISO 8601 with trailing Z (UTC)."""
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def row_to_dict(row):
    return {
        'id': row[0],
        'name': row[1],
        'description': row[2],
        'apiEndpoint': row[3],
        'fields': row[4],
        'createdAt': format_ts(row[5]),
        'updatedAt': format_ts(row[6]),
        'exportScripts': row[7] if len(row) > 7 else [],
        'rowExportScripts': row[8] if len(row) > 8 else [],
        'apiPublic': row[9] if len(row) > 9 else False,
        'validationScript': row[10] if len(row) > 10 else None,
        'apiWritable': row[11] if len(row) > 11 else False,
        'viewConfig': row[12] if len(row) > 12 else {},
        'deleteBinding': row[13] if len(row) > 13 else None,
    }


@page_configs_bp.route('/pageConfigs', methods=['GET'])
@login_required
def list_page_configs():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, description, api_endpoint, fields, created_at, updated_at, export_scripts, row_export_scripts, api_public, validation_script, api_writable, view_config, delete_binding FROM page_configs ORDER BY created_at')
        rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@page_configs_bp.route('/pageConfigs/<config_id>', methods=['GET'])
@login_required
def get_page_config(config_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, description, api_endpoint, fields, created_at, updated_at, export_scripts, row_export_scripts, api_public, validation_script, api_writable, view_config, delete_binding FROM page_configs WHERE id = %s', (config_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row_to_dict(row))


@page_configs_bp.route('/pageConfigs', methods=['POST'])
@admin_required
def create_page_config():
    body = request.get_json(force=True)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO page_configs (id, name, description, api_endpoint, fields, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
            (body.get('id'), body.get('name'), body.get('description'), body.get('apiEndpoint'),
             psycopg2.extras.Json(body.get('fields', [])),
             body.get('createdAt'), body.get('updatedAt')),
        )
    log_operation('create', 'page_config', body.get('id'), body.get('name'),
                  f'新增页面配置「{body.get("name")}」')
    return jsonify(body), 201


@page_configs_bp.route('/pageConfigs/<config_id>', methods=['PUT'])
@admin_required
def update_page_config(config_id):
    body = request.get_json(force=True)
    with get_db() as conn:
        cur = conn.cursor()
        # Build SET clause dynamically to avoid overwriting fields not present in the body
        sets = []
        params = []
        if 'name' in body:
            sets.append('name=%s')
            params.append(body['name'])
        if 'description' in body:
            sets.append('description=%s')
            params.append(body['description'])
        if 'apiEndpoint' in body:
            sets.append('api_endpoint=%s')
            params.append(body['apiEndpoint'])
        if 'fields' in body:
            sets.append('fields=%s')
            params.append(psycopg2.extras.Json(body['fields']))
        if 'exportScripts' in body:
            sets.append('export_scripts=%s')
            params.append(psycopg2.extras.Json(body['exportScripts']))
        if 'rowExportScripts' in body:
            sets.append('row_export_scripts=%s')
            params.append(psycopg2.extras.Json(body['rowExportScripts']))
        if 'apiPublic' in body:
            sets.append('api_public=%s')
            params.append(body['apiPublic'])
        if 'validationScript' in body:
            sets.append('validation_script=%s')
            params.append(body['validationScript'])
        if 'apiWritable' in body:
            sets.append('api_writable=%s')
            params.append(body['apiWritable'])
        if 'viewConfig' in body:
            sets.append('view_config=%s')
            params.append(psycopg2.extras.Json(body['viewConfig']))
        if 'deleteBinding' in body:
            sets.append('delete_binding=%s')
            params.append(psycopg2.extras.Json(body['deleteBinding']))
        if 'updatedAt' in body:
            sets.append('updated_at=%s')
            params.append(body['updatedAt'])

        if sets:
            params.append(config_id)
            cur.execute(f'UPDATE page_configs SET {", ".join(sets)} WHERE id=%s', params)

        # Return full record
        cur.execute('SELECT id, name, description, api_endpoint, fields, created_at, updated_at, export_scripts, row_export_scripts, api_public, validation_script, api_writable, view_config, delete_binding FROM page_configs WHERE id = %s', (config_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    log_operation('update', 'page_config', config_id, row_to_dict(row)['name'],
                  f'修改页面配置「{row_to_dict(row)["name"]}」')
    return jsonify(row_to_dict(row))


@page_configs_bp.route('/pageConfigs/<config_id>', methods=['DELETE'])
@admin_required
def delete_page_config(config_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name FROM page_configs WHERE id = %s', (config_id,))
        row = cur.fetchone()
        config_name = row[0] if row else config_id
        cur.execute('DELETE FROM page_configs WHERE id = %s', (config_id,))
    log_operation('delete', 'page_config', config_id, config_name,
                  f'删除页面配置「{config_name}」')
    return jsonify({})


@page_configs_bp.route('/pageConfigs/<page_id>/relations', methods=['GET'])
@login_required
def get_relations(page_id):
    """获取页面配置的关联关系图谱"""
    # 强制重新导入模块（解决缓存问题）
    import importlib
    import utils.page_config_relations
    importlib.reload(utils.page_config_relations)
    from utils.page_config_relations import get_page_config_relations as get_relations_func

    try:
        # 验证depth参数
        depth = request.args.get('depth', '3')
        try:
            depth = int(depth)
            if depth < 1 or depth > 10:
                return jsonify({'error': 'depth参数必须在1-10之间'}), 400
        except ValueError:
            return jsonify({'error': 'depth参数必须是整数'}), 400

        result = get_relations_func(page_id, max_depth=depth)

        if len(result['nodes']) == 0:
            return jsonify({'error': '页面配置不存在'}), 404

        if len(result['nodes']) > 50:
            return jsonify({
                'error': '关联节点过多（>50），建议减少递归深度',
                'hint': '请使用depth参数限制层级'
            }), 400

        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e)}), 500
