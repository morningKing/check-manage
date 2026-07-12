from flask import Blueprint, request, jsonify
from db import get_db
from datetime import datetime, timezone
from auth import login_required, require_permission
from utils.operation_log import log_operation
from utils.page_config_relations import get_page_config_relations
from utils.field_indexes import sync_field_indexes, mark_all_dropping
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
@require_permission('admin.page_configs')
def create_page_config():
    body = request.get_json(force=True)
    config_id = body.get('id')
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO page_configs (id, name, description, api_endpoint, fields, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
            (config_id, body.get('name'), body.get('description'), body.get('apiEndpoint'),
             psycopg2.extras.Json(body.get('fields', [])),
             body.get('createdAt'), body.get('updatedAt')),
        )
        if body.get('fields'):
            collection = config_id.replace('page-', '', 1) if config_id and config_id.startswith('page-') else config_id
            sync_field_indexes(cur, collection, body['fields'])
    log_operation('create', 'page_config', body.get('id'), body.get('name'),
                  f'新增页面配置「{body.get("name")}」')
    return jsonify(body), 201


@page_configs_bp.route('/pageConfigs/<config_id>/has-data', methods=['GET'])
@require_permission('admin.page_configs')
def page_config_has_data(config_id):
    """Return whether the collection backing this page has any rows.

    Used by the page-config editor to lock existing field definitions:
    once data exists, field name/controlType/etc. can't change.
    """
    collection = config_id.replace('page-', '', 1) if config_id.startswith('page-') else config_id
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT 1 FROM dynamic_data WHERE collection = %s LIMIT 1', (collection,))
        has_data = cur.fetchone() is not None
    return jsonify({"hasData": has_data})


@page_configs_bp.route('/pageConfigs/<config_id>', methods=['PUT'])
@require_permission('admin.page_configs')
def update_page_config(config_id):
    body = request.get_json(force=True)
    with get_db() as conn:
        cur = conn.cursor()

        # If the page already has data rows, lock existing field definitions:
        # only ADDING new fields is allowed. This protects data consistency
        # (renaming or retyping a field would orphan stored JSONB values).
        if 'fields' in body:
            collection = config_id.replace('page-', '', 1) if config_id.startswith('page-') else config_id
            cur.execute(
                'SELECT 1 FROM dynamic_data WHERE collection = %s LIMIT 1',
                (collection,),
            )
            has_data = cur.fetchone() is not None
            if has_data:
                cur.execute('SELECT fields FROM page_configs WHERE id = %s', (config_id,))
                row = cur.fetchone()
                current_fields = (row[0] if row else []) or []
                new_fields = body['fields'] or []
                current_by_name = {f.get('fieldName'): f for f in current_fields if isinstance(f, dict)}
                new_by_name = {f.get('fieldName'): f for f in new_fields if isinstance(f, dict)}
                # 1. Removed fields
                removed = [n for n in current_by_name if n not in new_by_name]
                if removed:
                    return jsonify({
                        "error": "该页面已存在数据,不能删除已有字段",
                        "code": "FIELDS_LOCKED",
                        "removedFields": removed,
                    }), 409
                # 2. Modified fields (any non-identical attribute on an existing field).
                # 'indexed' 是纯性能维度的开关，不改变已存数据的形状/兼容性，允许
                # 单独在已有数据的页面上修改（其余属性变化仍然锁定）——这也是这个
                # 功能真正有意义的场景：数据量已经大到需要加索引的页面。
                modified = []
                for name, cur_field in current_by_name.items():
                    new_field = new_by_name.get(name)
                    if new_field != cur_field:
                        cur_sans_indexed = {k: v for k, v in cur_field.items() if k != 'indexed'}
                        new_sans_indexed = {k: v for k, v in (new_field or {}).items() if k != 'indexed'}
                        if cur_sans_indexed != new_sans_indexed:
                            modified.append(name)
                if modified:
                    return jsonify({
                        "error": "该页面已存在数据,只能新增字段,不能修改已有字段的任何属性",
                        "code": "FIELDS_LOCKED",
                        "modifiedFields": modified,
                    }), 409

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

        # 字段配置里 indexed 标记的变化，同步进 field_indexes（后台任务异步建/删索引）
        if 'fields' in body:
            sync_field_indexes(cur, collection, body['fields'])

        # Return full record
        cur.execute('SELECT id, name, description, api_endpoint, fields, created_at, updated_at, export_scripts, row_export_scripts, api_public, validation_script, api_writable, view_config, delete_binding FROM page_configs WHERE id = %s', (config_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    log_operation('update', 'page_config', config_id, row_to_dict(row)['name'],
                  f'修改页面配置「{row_to_dict(row)["name"]}」')
    return jsonify(row_to_dict(row))


@page_configs_bp.route('/pageConfigs/<config_id>', methods=['DELETE'])
@require_permission('admin.page_configs')
def delete_page_config(config_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name FROM page_configs WHERE id = %s', (config_id,))
        row = cur.fetchone()
        config_name = row[0] if row else config_id
        cur.execute('DELETE FROM page_configs WHERE id = %s', (config_id,))
        collection = config_id.replace('page-', '', 1) if config_id.startswith('page-') else config_id
        mark_all_dropping(cur, collection)
    log_operation('delete', 'page_config', config_id, config_name,
                  f'删除页面配置「{config_name}」')
    return jsonify({})


@page_configs_bp.route('/pageConfigs/<config_id>/field-indexes', methods=['GET'])
@login_required
def get_field_index_status(config_id):
    """字段索引构建状态（供管理端字段配置界面轮询展示：待建/构建中/已就绪/失败）。"""
    collection = config_id.replace('page-', '', 1) if config_id.startswith('page-') else config_id
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT field_name, status, error, requested_at, ready_at '
            'FROM field_indexes WHERE collection = %s',
            (collection,),
        )
        rows = cur.fetchall()
    return jsonify({
        'data': [
            {
                'fieldName': r[0],
                'status': r[1],
                'error': r[2],
                'requestedAt': format_ts(r[3]),
                'readyAt': format_ts(r[4]),
            }
            for r in rows
        ]
    })


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
