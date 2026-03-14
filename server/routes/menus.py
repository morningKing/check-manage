from flask import Blueprint, request, jsonify
from db import get_db
from auth import login_required, admin_required
from utils.operation_log import log_operation
import json

menus_bp = Blueprint('menus', __name__)

COLUMNS = ['id', 'name', 'icon', 'page_id', 'parent_id', '"order"', 'path', 'roles', 'export_script_id']
CAMEL_KEYS = ['id', 'name', 'icon', 'pageId', 'parentId', 'order', 'path', 'roles', 'exportScriptId']


def row_to_dict(row):
    result = {CAMEL_KEYS[i]: row[i] for i in range(len(CAMEL_KEYS))}
    # Convert None to None for exportScriptId
    return result


def _is_descendant_of(cur, menu_id, ancestor_id):
    """判断 menu_id 是否为 ancestor_id 本身或其后代"""
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


def _is_builtin_menu(cur, menu_id):
    """判断菜单是否为内置菜单（首页 + 系统配置树）"""
    if menu_id == 'menu-1':
        return True
    return _is_descendant_of(cur, menu_id, 'menu-3')


@menus_bp.route('/menus', methods=['GET'])
@login_required
def list_menus():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, icon, page_id, parent_id, "order", path, roles, export_script_id FROM menus ORDER BY "order"')
        rows = cur.fetchall()
        # Also fetch export script names for display
        result = []
        for row in rows:
            menu_dict = row_to_dict(row)
            if row[8]:  # export_script_id
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
        cur.execute('SELECT id, name, icon, page_id, parent_id, "order", path, roles, export_script_id FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row_to_dict(row))


@menus_bp.route('/menus', methods=['POST'])
@admin_required
def create_menu():
    body = request.get_json(force=True)
    parent_id = body.get('parentId')

    # 系统配置下不允许增加子菜单
    if parent_id:
        with get_db() as conn:
            cur = conn.cursor()
            if _is_descendant_of(cur, parent_id, 'menu-3'):
                return jsonify({"error": "系统配置菜单下不允许添加子菜单"}), 403

    roles = body.get('roles', ['admin', 'developer', 'guest'])
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, export_script_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (body.get('id'), body.get('name'), body.get('icon'), body.get('pageId'),
             body.get('parentId'), body.get('order', 0), body.get('path'), json.dumps(roles), body.get('exportScriptId')),
        )
    body['roles'] = roles
    log_operation('create', 'menu', body.get('id'), body.get('name'),
                  f'新增菜单「{body.get("name")}」')
    return jsonify(body), 201


@menus_bp.route('/menus/<menu_id>', methods=['PUT'])
@admin_required
def update_menu(menu_id):
    # 内置菜单不允许编辑
    with get_db() as conn:
        cur = conn.cursor()
        if _is_builtin_menu(cur, menu_id):
            return jsonify({"error": "内置菜单不允许编辑"}), 403

    body = request.get_json(force=True)
    new_parent_id = body.get('parentId')

    # 验证父级菜单
    if new_parent_id:
        with get_db() as conn:
            cur = conn.cursor()

            # 不能将自己设为父级
            if new_parent_id == menu_id:
                return jsonify({"error": "不能将自己设为父级菜单"}), 400

            # 不能将子菜单设为父级（循环引用）
            if _is_descendant_of(cur, new_parent_id, menu_id):
                return jsonify({"error": "不能将子菜单设为父级菜单"}), 400

            # 检查层级限制（最多3级）
            # 计算新父级的层级
            parent_level = 0
            current_id = new_parent_id
            visited = set()
            while current_id:
                if current_id in visited:
                    break
                visited.add(current_id)
                cur.execute('SELECT parent_id FROM menus WHERE id = %s', (current_id,))
                row = cur.fetchone()
                if not row:
                    break
                parent_level += 1
                current_id = row[0]

            # 如果新父级已经是第3级，不允许
            if parent_level >= 3:
                return jsonify({"error": "父级菜单层级过深（最多支持3级菜单）"}), 400

            # 检查新父级是否是内置菜单
            if _is_builtin_menu(cur, new_parent_id):
                return jsonify({"error": "不能将菜单移到内置菜单下"}), 403

    roles = body.get('roles', ['admin', 'developer', 'guest'])
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE menus SET name=%s, icon=%s, page_id=%s, parent_id=%s, "order"=%s, path=%s, roles=%s, export_script_id=%s WHERE id=%s',
            (body.get('name'), body.get('icon'), body.get('pageId'),
             body.get('parentId'), body.get('order', 0), body.get('path'), json.dumps(roles), body.get('exportScriptId'), menu_id),
        )
    body['id'] = menu_id
    body['roles'] = roles
    log_operation('update', 'menu', menu_id, body.get('name'),
                  f'修改菜单「{body.get("name")}」')
    return jsonify(body)


@menus_bp.route('/menus/<menu_id>', methods=['DELETE'])
@admin_required
def delete_menu(menu_id):
    # 内置菜单不允许删除
    with get_db() as conn:
        cur = conn.cursor()
        if _is_builtin_menu(cur, menu_id):
            return jsonify({"error": "内置菜单不允许删除"}), 403

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
        menu_name = row[0] if row else menu_id
        cur.execute('DELETE FROM menus WHERE id = %s', (menu_id,))
    log_operation('delete', 'menu', menu_id, menu_name,
                  f'删除菜单「{menu_name}」')
    return jsonify({})


@menus_bp.route('/menus/<menu_id>/exportScript', methods=['PUT'])
@admin_required
def set_menu_export_script(menu_id):
    """设置菜单的导出脚本绑定"""
    body = request.get_json(force=True)
    script_id = body.get('exportScriptId')  # Can be None to unbind

    with get_db() as conn:
        cur = conn.cursor()
        # Verify menu exists
        cur.execute('SELECT name FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "菜单不存在"}), 404
        menu_name = row[0]

        # If script_id provided, verify it exists
        if script_id:
            cur.execute('SELECT name FROM export_scripts WHERE id = %s', (script_id,))
            script_row = cur.fetchone()
            if not script_row:
                return jsonify({"error": "导出脚本不存在"}), 404

        # Update menu
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
    """获取菜单导出预览信息"""
    from utils.menu_export import get_menu_collections_with_info

    with get_db() as conn:
        cur = conn.cursor()
        # Get menu info
        cur.execute('SELECT name, export_script_id FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "菜单不存在"}), 404

        menu_name = row[0]
        bound_script_id = row[1]

        # Get all collections under this menu
        pages = get_menu_collections_with_info(cur, menu_id)

        # Get bound script info
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
