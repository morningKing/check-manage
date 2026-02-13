from flask import Blueprint, request, jsonify
from db import get_db
from auth import login_required, admin_required
from utils.operation_log import log_operation
import json

menus_bp = Blueprint('menus', __name__)

COLUMNS = ['id', 'name', 'icon', 'page_id', 'parent_id', '"order"', 'path', 'roles']
CAMEL_KEYS = ['id', 'name', 'icon', 'pageId', 'parentId', 'order', 'path', 'roles']


def row_to_dict(row):
    return {CAMEL_KEYS[i]: row[i] for i in range(len(CAMEL_KEYS))}


@menus_bp.route('/menus', methods=['GET'])
@login_required
def list_menus():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, icon, page_id, parent_id, "order", path, roles FROM menus ORDER BY "order"')
        rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@menus_bp.route('/menus/<menu_id>', methods=['GET'])
@login_required
def get_menu(menu_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, icon, page_id, parent_id, "order", path, roles FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row_to_dict(row))


@menus_bp.route('/menus', methods=['POST'])
@admin_required
def create_menu():
    body = request.get_json(force=True)
    roles = body.get('roles', ['admin', 'developer', 'guest'])
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
            (body.get('id'), body.get('name'), body.get('icon'), body.get('pageId'),
             body.get('parentId'), body.get('order', 0), body.get('path'), json.dumps(roles)),
        )
    body['roles'] = roles
    log_operation('create', 'menu', body.get('id'), body.get('name'),
                  f'新增菜单「{body.get("name")}」')
    return jsonify(body), 201


@menus_bp.route('/menus/<menu_id>', methods=['PUT'])
@admin_required
def update_menu(menu_id):
    body = request.get_json(force=True)
    roles = body.get('roles', ['admin', 'developer', 'guest'])
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE menus SET name=%s, icon=%s, page_id=%s, parent_id=%s, "order"=%s, path=%s, roles=%s WHERE id=%s',
            (body.get('name'), body.get('icon'), body.get('pageId'),
             body.get('parentId'), body.get('order', 0), body.get('path'), json.dumps(roles), menu_id),
        )
    body['id'] = menu_id
    body['roles'] = roles
    log_operation('update', 'menu', menu_id, body.get('name'),
                  f'修改菜单「{body.get("name")}」')
    return jsonify(body)


@menus_bp.route('/menus/<menu_id>', methods=['DELETE'])
@admin_required
def delete_menu(menu_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name FROM menus WHERE id = %s', (menu_id,))
        row = cur.fetchone()
        menu_name = row[0] if row else menu_id
        cur.execute('DELETE FROM menus WHERE id = %s', (menu_id,))
    log_operation('delete', 'menu', menu_id, menu_name,
                  f'删除菜单「{menu_name}」')
    return jsonify({})
