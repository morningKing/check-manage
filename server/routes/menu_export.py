"""
菜单级导出 API 路由

端点：
- POST /menuExport - 批量导出多个菜单的数据
"""

from flask import Blueprint, request, jsonify, Response
from db import get_db
from auth import login_required, write_required
from utils.menu_export import execute_menu_export, batch_clear
from utils.operation_log import log_operation
from urllib.parse import quote

menu_export_bp = Blueprint('menu_export', __name__)


@menu_export_bp.route('/menuExport', methods=['POST'])
@login_required
def export_menus():
    """
    批量导出多个菜单的数据

    请求体：
    {
        "menuIds": ["menu-2"],      // 选中的菜单ID列表
        "scriptId": "script-xxx",  // 可选，指定导出脚本（覆盖菜单绑定）
        "branchId": "main"         // 可选，指定分支ID（默认 main）
    }

    返回：ZIP 文件
    """
    body = request.get_json(force=True)
    menu_ids = body.get('menuIds', [])
    script_id = body.get('scriptId')  # Optional override
    branch_id = body.get('branchId', 'main')  # NEW: branch filter

    if not menu_ids:
        return jsonify({'error': '未选择菜单'}), 400

    with get_db() as conn:
        zip_bytes, zip_filename, errors = execute_menu_export(conn, menu_ids, script_id, branch_id)

    if zip_bytes is None:
        error_msg = '所有导出任务均失败'
        if errors:
            error_msg += '：' + '; '.join(errors[:5])  # Show first 5 errors
            if len(errors) > 5:
                error_msg += f'... 等共 {len(errors)} 个错误'
        return jsonify({'error': error_msg}), 400

    # Include errors in response headers if any
    headers = {
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(zip_filename)}",
        'Content-Length': str(len(zip_bytes)),
    }

    # If there were errors, include a warning header
    if errors:
        headers['X-Export-Errors'] = quote('; '.join(errors[:3]))

    return Response(
        zip_bytes,
        mimetype='application/zip',
        headers=headers,
    )


@menu_export_bp.route('/menuExport/availableMenus', methods=['GET'])
@login_required
def get_available_menus():
    """
    获取可用于导出的菜单树（仅包含动态数据表）

    只返回叶子菜单有动态数据表（page_id 以 'page-' 开头）的菜单，
    过滤掉系统配置、数据工具等静态页面菜单。

    返回：菜单树结构
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 获取所有菜单
        cur.execute('''
            SELECT id, name, icon, page_id, parent_id, "order", export_script_id
            FROM menus
            ORDER BY "order"
        ''')
        all_menus = cur.fetchall()

        # 找出所有有动态数据表的菜单ID（page_id 以 'page-' 开头）
        dynamic_menu_ids = set()
        for row in all_menus:
            page_id = row[3]
            if page_id and page_id.startswith('page-'):
                dynamic_menu_ids.add(row[0])

        # 向上标记所有有动态数据表子菜单的父菜单
        def mark_parent_menus(menu_id):
            for row in all_menus:
                if row[0] == menu_id and row[4]:  # 有 parent_id
                    parent_id = row[4]
                    if parent_id not in dynamic_menu_ids:
                        dynamic_menu_ids.add(parent_id)
                        mark_parent_menus(parent_id)

        for menu_id in list(dynamic_menu_ids):
            mark_parent_menus(menu_id)

        # 构建菜单树，只包含有动态数据表的菜单
        def build_tree(parent_id=None):
            result = []
            for row in all_menus:
                if row[4] != parent_id:  # parent_id 匹配
                    continue
                menu_id = row[0]
                if menu_id not in dynamic_menu_ids:
                    continue

                # 获取导出脚本名称
                export_script_name = None
                if row[6]:  # export_script_id
                    cur.execute(
                        'SELECT name FROM export_scripts WHERE id = %s',
                        (row[6],)
                    )
                    script_row = cur.fetchone()
                    if script_row:
                        export_script_name = script_row[0]

                menu_item = {
                    'id': menu_id,
                    'name': row[1],
                    'icon': row[2],
                    'pageId': row[3],
                    'parentId': row[4],
                    'order': row[5],
                    'exportScriptId': row[6],
                    'exportScriptName': export_script_name,
                }

                # 递归构建子菜单
                children = build_tree(menu_id)
                if children:
                    menu_item['children'] = children

                result.append(menu_item)

            return result

        menu_tree = build_tree()

    return jsonify(menu_tree)


@menu_export_bp.route('/menuExport/preview', methods=['POST'])
@login_required
def preview_menu_export():
    """
    预览菜单导出信息

    请求体：
    {
        "menuIds": ["menu-2"],
        "branchId": "main"  // 可选，指定分支ID（默认 main）
    }

    返回：
    {
        "menus": [
            {
                "menuId": "menu-2",
                "menuName": "巡检管理",
                "pages": [...],
                "boundScript": {...},
                "totalRecords": 27001
            }
        ],
        "totalRecords": 27001,
        "availableScripts": [...]
    }
    """
    from utils.menu_export import get_menu_collections_with_info

    body = request.get_json(force=True)
    menu_ids = body.get('menuIds', [])
    branch_id = body.get('branchId', 'main')  # NEW: branch filter

    if not menu_ids:
        return jsonify({'error': '未选择菜单'}), 400

    with get_db() as conn:
        cur = conn.cursor()

        # Get available export scripts
        cur.execute('SELECT id, name, description FROM export_scripts ORDER BY name')
        available_scripts = [
            {'id': row[0], 'name': row[1], 'description': row[2]}
            for row in cur.fetchall()
        ]

        menus_info = []
        total_all_records = 0

        for menu_id in menu_ids:
            # Get menu info
            cur.execute(
                'SELECT name, export_script_id FROM menus WHERE id = %s',
                (menu_id,)
            )
            row = cur.fetchone()
            if not row:
                continue

            menu_name = row[0]
            bound_script_id = row[1]

            # Get pages under this menu (filtered by branch)
            pages = get_menu_collections_with_info(cur, menu_id, branch_id)

            # Get bound script info
            bound_script = None
            if bound_script_id:
                cur.execute(
                    'SELECT id, name FROM export_scripts WHERE id = %s',
                    (bound_script_id,)
                )
                script_row = cur.fetchone()
                if script_row:
                    bound_script = {'id': script_row[0], 'name': script_row[1]}

            total_records = sum(p['recordCount'] for p in pages)
            total_all_records += total_records

            menus_info.append({
                'menuId': menu_id,
                'menuName': menu_name,
                'pages': pages,
                'boundScript': bound_script,
                'totalRecords': total_records
            })

    return jsonify({
        'menus': menus_info,
        'totalRecords': total_all_records,
        'availableScripts': available_scripts
    })


@menu_export_bp.route('/menuExport/batchClear', methods=['POST'])
@write_required
def batch_clear_collections():
    """批量清空多个数据页（collection）在指定分支的全部记录。"""
    body = request.get_json(force=True)
    collections = body.get('collections', [])
    branch_id = body.get('branchId', 'main')

    if not collections:
        return jsonify({'error': '未选择要清空的数据页'}), 400

    with get_db() as conn:
        result = batch_clear(conn, collections, branch_id)

    log_operation(
        'delete', 'dynamic_data', None, None,
        f"批量清空 {len(collections)} 个数据页，共 {result['totalDeleted']} 条记录"
        f"（分支 {branch_id}）",
        branch_id=branch_id,
    )

    return jsonify(result)