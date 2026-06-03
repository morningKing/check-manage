from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import require_permission, login_required
from utils.permissions import PERMISSION_CATALOG, catalog_keys, invalidate_cache
from utils.operation_log import log_operation
import psycopg2.extras
import uuid

roles_bp = Blueprint('roles', __name__)


def _role_to_dict(row):
    return {
        'id': row[0], 'name': row[1], 'description': row[2] or '',
        'isSystem': row[3], 'isSuperuser': row[4], 'defaultPageAccess': row[5],
    }


@roles_bp.route('/roles/catalog', methods=['GET'])
@require_permission('admin.roles')
def get_catalog():
    return jsonify(PERMISSION_CATALOG)


@roles_bp.route('/roles/options', methods=['GET'])
@login_required
def role_options():
    """轻量角色清单（id + 显示名 + 标记），供菜单管理/用户管理等下拉选择器使用。
    仅需登录即可读取（角色名不敏感），不要求 admin.roles。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, is_system, is_superuser FROM roles '
                    'ORDER BY is_system DESC, created_at')
        rows = cur.fetchall()
    return jsonify([
        {'id': r[0], 'name': r[1], 'isSystem': r[2], 'isSuperuser': r[3]}
        for r in rows
    ])


@roles_bp.route('/roles', methods=['GET'])
@require_permission('admin.roles')
def list_roles():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, description, is_system, is_superuser, '
                    'default_page_access FROM roles ORDER BY is_system DESC, created_at')
        rows = cur.fetchall()
    return jsonify([_role_to_dict(r) for r in rows])


@roles_bp.route('/roles/<role_id>', methods=['GET'])
@require_permission('admin.roles')
def get_role(role_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, description, is_system, is_superuser, '
                    'default_page_access FROM roles WHERE id = %s', (role_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '角色不存在'}), 404
        cur.execute('SELECT permission_key FROM role_permissions WHERE role_id = %s', (role_id,))
        admin_keys = [r[0] for r in cur.fetchall()]
        cur.execute('SELECT page_id, can_read, can_create, can_update, can_delete '
                    'FROM role_page_permissions WHERE role_id = %s', (role_id,))
        pages = [{'pageId': r[0], 'canRead': r[1], 'canCreate': r[2],
                  'canUpdate': r[3], 'canDelete': r[4]} for r in cur.fetchall()]
    data = _role_to_dict(row)
    data['adminKeys'] = admin_keys
    data['pagePermissions'] = pages
    return jsonify(data)


@roles_bp.route('/roles', methods=['POST'])
@require_permission('admin.roles')
def create_role():
    body = request.get_json(force=True)
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': '角色名称不能为空'}), 400
    default_access = body.get('defaultPageAccess', 'read')
    if default_access not in ('none', 'read', 'write'):
        return jsonify({'error': '无效的默认数据页权限'}), 400
    role_id = f'role-{uuid.uuid4().hex[:8]}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO roles (id, name, description, is_system, is_superuser, default_page_access) '
            'VALUES (%s,%s,%s,FALSE,FALSE,%s)',
            (role_id, name, body.get('description', ''), default_access),
        )
    log_operation('create', 'role', role_id, name, f'新增角色「{name}」')
    invalidate_cache(role_id)
    return jsonify({'id': role_id, 'name': name}), 201


@roles_bp.route('/roles/<role_id>', methods=['PUT'])
@require_permission('admin.roles')
def update_role(role_id):
    body = request.get_json(force=True)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT is_superuser, is_system FROM roles WHERE id = %s', (role_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '角色不存在'}), 404
        is_superuser = row[0]

        # Update scalar fields
        sets, params = [], []
        if 'name' in body and body['name'].strip():
            sets.append('name = %s'); params.append(body['name'].strip())
        if 'description' in body:
            sets.append('description = %s'); params.append(body['description'])
        if 'defaultPageAccess' in body:
            if body['defaultPageAccess'] not in ('none', 'read', 'write'):
                return jsonify({'error': '无效的默认数据页权限'}), 400
            sets.append('default_page_access = %s'); params.append(body['defaultPageAccess'])
        if sets:
            sets.append('updated_at = NOW()')
            params.append(role_id)
            cur.execute(f'UPDATE roles SET {", ".join(sets)} WHERE id = %s', params)

        # Replace admin permission keys (superuser keeps all — ignore inbound changes)
        if 'adminKeys' in body and not is_superuser:
            valid = set(catalog_keys())
            keys = [k for k in body['adminKeys'] if k in valid]
            cur.execute('DELETE FROM role_permissions WHERE role_id = %s', (role_id,))
            for k in keys:
                cur.execute('INSERT INTO role_permissions (role_id, permission_key) '
                            'VALUES (%s,%s) ON CONFLICT DO NOTHING', (role_id, k))

        # Replace page permissions (superuser bypasses anyway)
        if 'pagePermissions' in body and not is_superuser:
            cur.execute('DELETE FROM role_page_permissions WHERE role_id = %s', (role_id,))
            for pp in body['pagePermissions']:
                cur.execute(
                    'INSERT INTO role_page_permissions '
                    '(role_id, page_id, can_read, can_create, can_update, can_delete) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (role_id, pp['pageId'], bool(pp.get('canRead', True)),
                     bool(pp.get('canCreate', False)), bool(pp.get('canUpdate', False)),
                     bool(pp.get('canDelete', False))),
                )
    log_operation('update', 'role', role_id, body.get('name', role_id), f'更新角色权限「{role_id}」')
    invalidate_cache(role_id)
    return jsonify({'message': '更新成功'})


@roles_bp.route('/roles/<role_id>', methods=['DELETE'])
@require_permission('admin.roles')
def delete_role(role_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name, is_system FROM roles WHERE id = %s', (role_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '角色不存在'}), 404
        if row[1]:
            return jsonify({'error': '内置角色不可删除'}), 400
        cur.execute('SELECT username FROM users WHERE role = %s', (role_id,))
        users = [r[0] for r in cur.fetchall()]
        if users:
            return jsonify({'error': f'该角色仍被用户使用：{", ".join(users)}，请先重新分配'}), 409
        # Scrub the slug from all menus.roles arrays (transactional with the delete)
        cur.execute("UPDATE menus SET roles = roles - %s WHERE roles ? %s", (role_id, role_id))
        cur.execute('DELETE FROM roles WHERE id = %s', (role_id,))
    log_operation('delete', 'role', role_id, row[0], f'删除角色「{row[0]}」')
    invalidate_cache(role_id)
    return jsonify({})


@roles_bp.route('/roles/<role_id>/menu-visibility', methods=['PUT'])
@require_permission('admin.roles')
def update_menu_visibility(role_id):
    """设置某角色可见的菜单集合（从角色侧维护 menus.roles）。

    Body: {"menuIds": [...]}  —— 该角色应当能看到的菜单 id 列表。
    对每个菜单：在列表里就把角色 slug 加进 menus.roles，否则移除。
    """
    body = request.get_json(force=True)
    menu_ids = set(body.get('menuIds', []))
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name, is_superuser FROM roles WHERE id = %s', (role_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '角色不存在'}), 404
        # 超级管理员永远可见全部菜单，无需写库
        if row[1]:
            return jsonify({'message': '超级管理员可见全部菜单，无需配置'})
        cur.execute('SELECT id FROM menus')
        all_ids = [r[0] for r in cur.fetchall()]
        for mid in all_ids:
            if mid in menu_ids:
                cur.execute(
                    "UPDATE menus SET roles = roles || %s WHERE id = %s AND NOT (roles ? %s)",
                    (psycopg2.extras.Json([role_id]), mid, role_id),
                )
            else:
                cur.execute(
                    "UPDATE menus SET roles = roles - %s WHERE id = %s AND roles ? %s",
                    (role_id, mid, role_id),
                )
    log_operation('update', 'role', role_id, row[0], f'更新角色「{row[0]}」菜单可见性')
    return jsonify({'message': '已保存'})
