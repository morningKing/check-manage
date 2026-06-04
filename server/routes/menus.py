from flask import Blueprint, request, jsonify
from db import get_db
from auth import login_required, require_permission
from utils.operation_log import log_operation
import json

menus_bp = Blueprint('menus', __name__)

COLUMNS = ['id', 'name', 'icon', 'page_id', 'parent_id', '"order"', 'path', 'roles', 'export_script_id', 'menu_type', 'project_id']
CAMEL_KEYS = ['id', 'name', 'icon', 'pageId', 'parentId', 'order', 'path', 'roles', 'exportScriptId', 'menuType', 'projectId']

MENU_TYPES = {
    'system': {'name': '系统菜单', 'level': 1, 'editable': False, 'deletable': False},
    'workspace': {'name': '工作空间', 'level': 1, 'editable': True, 'deletable': True, 'allowed_child_types': ['project']},
    'project': {'name': '项目', 'level': 2, 'editable': True, 'deletable': True, 'allowed_parent_types': ['workspace'], 'allowed_child_types': ['data']},
    'data': {'name': '数据菜单', 'level': 3, 'editable': True, 'deletable': True, 'allowed_parent_types': ['project'], 'requires_page_config': True},
}


def row_to_dict(row):
    if len(row) >= 11:
        return {
            'id': row[0],
            'name': row[1],
            'icon': row[2],
            'pageId': row[3],
            'parentId': row[4],
            'order': row[5],
            'path': row[6],
            'roles': row[7],
            'exportScriptId': row[8],
            'menuType': row[9],
            'projectId': row[10],
        }
    else:
        return {CAMEL_KEYS[i]: row[i] for i in range(len(row))}


def _is_descendant_of(cur, menu_id, ancestor_id):
    current = menu_id
    visited = set()
    while current:
        if current == ancestor_id:
            return True
        if current in visited:
            return False
        visited.add(current)
        cur.execute('SELECT parent_id FROM menus WHERE id = %s', (current,))
        row = cur.fetchone()
        current = row[0] if row else None
    return False


def _is_system_menu(cur, menu_id):
    cur.execute('SELECT menu_type FROM menus WHERE id = %s', (menu_id,))
    row = cur.fetchone()
    return row and row[0] == 'system'


def _get_menu_level(cur, menu_id):
    level = 0
    current_id = menu_id
    visited = set()
    while current_id:
        if current_id in visited:
            break
        visited.add(current_id)
        cur.execute('SELECT parent_id FROM menus WHERE id = %s', (current_id,))
        row = cur.fetchone()
        if not row:
            break
        level += 1
        current_id = row[0]
    return level


def _validate_menu_type(cur, menu_type, parent_id, page_id):
    if menu_type not in MENU_TYPES:
        return False, f"无效的菜单类型: {menu_type}"

    type_def = MENU_TYPES[menu_type]

    parent_type = None
    if parent_id:
        cur.execute('SELECT menu_type FROM menus WHERE id = %s', (parent_id,))
        row = cur.fetchone()
        if not row:
            return False, "父级菜单不存在"
        parent_type = row[0]

    allowed_parents = type_def.get('allowed_parent_types', [])
    if parent_id:
        if parent_type not in allowed_parents:
            return False, f"{type_def['name']} 的父级必须是 {allowed_parents}"
    else:
        if type_def['level'] != 1:
            return False, f"{type_def['name']} 必须有父级菜单"
        if menu_type not in ['system', 'workspace']:
            return False, f"一级菜单只能是系统菜单或工作空间"

    if menu_type == 'data' and not page_id:
        return False, "数据菜单必须关联页面配置"

    return True, None


@menus_bp.route('/menus', methods=['GET'])
@login_required
def list_menus():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, icon, page_id, parent_id, "order", path, roles, export_script_id, menu_type, project_id FROM menus ORDER BY "order"')
        rows = cur.fetchall()
        result = []
        for row in rows:
            menu_dict = row_to_dict(row)
            if row[8]:
                cur.execute('SELECT name FROM export_scripts WHERE id = %s', (row[8],))
                script_row = cur.fetchone()
                if script_row:
                    menu_dict['exportScriptName'] = script_row[0]
            result.append(menu_dict)
    return jsonify(result)


@menus_bp.route('/menus/<menu_id>', methods=['GET'])
@login_required
def get_menu(menu_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, icon, page_id, parent_id, "order", path, roles, export_script_id, menu_type, project_id FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row_to_dict(row))


@menus_bp.route('/menus', methods=['POST'])
@require_permission('admin.menus')
def create_menu():
    body = request.get_json(force=True)
    parent_id = body.get('parentId')
    menu_type = body.get('menuType', 'data')
    page_id = body.get('pageId')

    with get_db() as conn:
        cur = conn.cursor()

        valid, error = _validate_menu_type(cur, menu_type, parent_id, page_id)
        if not valid:
            return jsonify({"error": error}), 400

        project_id = None
        if menu_type == 'data' and parent_id:
            cur.execute('SELECT menu_type FROM menus WHERE id = %s', (parent_id,))
            row = cur.fetchone()
            if row and row[0] == 'project':
                project_id = parent_id

        roles = body.get('roles', ['admin', 'developer', 'guest'])
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, export_script_id, menu_type, project_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (body.get('id'), body.get('name'), body.get('icon'), body.get('pageId'),
             body.get('parentId'), body.get('order', 0), body.get('path'), json.dumps(roles), body.get('exportScriptId'),
             menu_type, project_id),
        )

    body['roles'] = roles
    body['menuType'] = menu_type
    body['projectId'] = project_id
    log_operation('create', 'menu', body.get('id'), body.get('name'),
                  f'新增菜单「{body.get("name")}」({MENU_TYPES[menu_type]["name"]})')
    return jsonify(body), 201


@menus_bp.route('/menus/<menu_id>', methods=['PUT'])
@require_permission('admin.menus')
def update_menu(menu_id):
    with get_db() as conn:
        cur = conn.cursor()

        if _is_system_menu(cur, menu_id):
            return jsonify({"error": "系统菜单不允许编辑"}), 403

        cur.execute('SELECT menu_type FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
        current_menu_type = row[0] if row else 'data'

    body = request.get_json(force=True)
    new_parent_id = body.get('parentId')
    menu_type = body.get('menuType', current_menu_type)
    page_id = body.get('pageId')

    if new_parent_id:
        with get_db() as conn:
            cur = conn.cursor()

            if new_parent_id == menu_id:
                return jsonify({"error": "不能将自己设为父级菜单"}), 400

            if _is_descendant_of(cur, new_parent_id, menu_id):
                return jsonify({"error": "不能将子菜单设为父级菜单"}), 400

            valid, error = _validate_menu_type(cur, menu_type, new_parent_id, page_id)
            if not valid:
                return jsonify({"error": error}), 400

            cur.execute('SELECT menu_type FROM menus WHERE id = %s', (new_parent_id,))
            row = cur.fetchone()
            if row and row[0] == 'system':
                return jsonify({"error": "不能将菜单移到系统菜单下"}), 403

    project_id = None
    if menu_type == 'data' and new_parent_id:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT menu_type FROM menus WHERE id = %s', (new_parent_id,))
            row = cur.fetchone()
            if row and row[0] == 'project':
                project_id = new_parent_id

    roles = body.get('roles', ['admin', 'developer', 'guest'])
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE menus SET name=%s, icon=%s, page_id=%s, parent_id=%s, "order"=%s, path=%s, roles=%s, export_script_id=%s, menu_type=%s, project_id=%s WHERE id=%s',
            (body.get('name'), body.get('icon'), body.get('pageId'),
             body.get('parentId'), body.get('order', 0), body.get('path'), json.dumps(roles), body.get('exportScriptId'),
             menu_type, project_id, menu_id),
        )
    body['id'] = menu_id
    body['roles'] = roles
    body['menuType'] = menu_type
    body['projectId'] = project_id
    log_operation('update', 'menu', menu_id, body.get('name'),
                  f'修改菜单「{body.get("name")}」')
    return jsonify(body)


@menus_bp.route('/menus/<menu_id>', methods=['DELETE'])
@require_permission('admin.menus')
def delete_menu(menu_id):
    with get_db() as conn:
        cur = conn.cursor()

        if _is_system_menu(cur, menu_id):
            return jsonify({"error": "系统菜单不允许删除"}), 403

        cur.execute('SELECT name FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
        menu_name = row[0] if row else menu_id
        cur.execute('DELETE FROM menus WHERE id = %s', (menu_id,))
    log_operation('delete', 'menu', menu_id, menu_name,
                  f'删除菜单「{menu_name}」')
    return jsonify({})


@menus_bp.route('/menus/<menu_id>/exportScript', methods=['PUT'])
@require_permission('admin.menus')
def set_menu_export_script(menu_id):
    body = request.get_json(force=True)
    script_id = body.get('exportScriptId')

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "菜单不存在"}), 404
        menu_name = row[0]

        if script_id:
            cur.execute('SELECT name FROM export_scripts WHERE id = %s', (script_id,))
            script_row = cur.fetchone()
            if not script_row:
                return jsonify({"error": "导出脚本不存在"}), 404

        cur.execute(
            'UPDATE menus SET export_script_id = %s WHERE id = %s',
            (script_id, menu_id),
        )

    script_name = None
    if script_id:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT name FROM export_scripts WHERE id = %s', (script_id,))
            script_row = cur.fetchone()
            if script_row:
                script_name = script_row[0]

    log_operation('update', 'menu', menu_id, menu_name,
                  f'设置菜单「{menu_name}」的导出脚本为「{script_name or "无"}」')

    return jsonify({
        "id": menu_id,
        "exportScriptId": script_id,
        "exportScriptName": script_name
    })


@menus_bp.route('/menus/<menu_id>/exportPreview', methods=['GET'])
@login_required
def get_menu_export_preview(menu_id):
    from utils.menu_export import get_menu_collections_with_info

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name, export_script_id FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "菜单不存在"}), 404

        menu_name = row[0]
        bound_script_id = row[1]

        pages = get_menu_collections_with_info(cur, menu_id)

        bound_script = None
        if bound_script_id:
            cur.execute('SELECT id, name FROM export_scripts WHERE id = %s', (bound_script_id,))
            script_row = cur.fetchone()
            if script_row:
                bound_script = {"id": script_row[0], "name": script_row[1]}

    total_records = sum(p['recordCount'] for p in pages)

    return jsonify({
        "menuName": menu_name,
        "pages": pages,
        "boundScript": bound_script,
        "totalRecords": total_records
    })