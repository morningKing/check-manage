"""导出脚本共享执行器（Flask-free，游标注入）。

Flask 路由与 MCP 工具都调用本模块，确保「绑定校验 + RBAC + 取数 + 引用解析 + 沙箱」
只有一份实现。不 import Flask、不 import 任何 db 模块——调用方传入游标。
"""
from datetime import timezone
from utils.script_runner import run_export_script, run_menu_export_script
from utils.export_references import resolve_page_references, resolve_references
from utils.menu_export import get_menu_collections

SCRIPT_SELECT = 'id, name, script, output_format, scope, bound_collection, bound_menu_id'


class ExportBindingError(Exception):
    """脚本目标与其绑定不符。"""


class ExportPermissionError(Exception):
    """当前角色无权导出该目标。"""


def _script_fields(script_row):
    # script_row 形如 (id, name, script, output_format, scope, bound_collection, bound_menu_id)
    return {
        'id': script_row[0], 'name': script_row[1], 'script': script_row[2],
        'output_format': script_row[3], 'scope': script_row[4] or 'page',
        'bound_collection': script_row[5], 'bound_menu_id': script_row[6],
    }


def check_binding(script_row, *, collection=None, menu_id=None):
    """校验脚本绑定：若脚本绑定了特定 collection/menu，调用方须提供匹配的目标。

    未绑定（bound_collection / bound_menu_id 为 None）的脚本允许任意目标。
    """
    s = _script_fields(script_row)
    if s['scope'] == 'menu':
        if s['bound_menu_id'] and menu_id != s['bound_menu_id']:
            raise ExportBindingError(
                f"脚本「{s['name']}」仅限其绑定菜单（{s['bound_menu_id']}），不能用于 {menu_id}")
    else:
        if s['bound_collection'] and collection != s['bound_collection']:
            raise ExportBindingError(
                f"脚本「{s['name']}」仅限其绑定数据页（{s['bound_collection']}），不能用于 {collection}")


def _menu_roles_for_collection(cur, collection):
    cur.execute(
        "SELECT roles FROM menus WHERE page_id = %s OR page_id = %s",
        (collection, f'page-{collection}'))
    row = cur.fetchone()
    return (row[0] or []) if row else []


def _menu_roles_for_menu(cur, menu_id):
    cur.execute("SELECT roles FROM menus WHERE id = %s", (menu_id,))
    row = cur.fetchone()
    return (row[0] or []) if row else []


def check_rbac(cur, *, collection=None, menu_id=None, role=None):
    """校验角色是否有权导出该目标。admin 始终放行。"""
    if role == 'admin':
        return
    roles = _menu_roles_for_menu(cur, menu_id) if menu_id else _menu_roles_for_collection(cur, collection)
    if role not in (roles or []):
        target = menu_id or collection
        raise ExportPermissionError(f"无权限导出：{target}")


def _fetch_page_data(cur, collection, branch_id, record_id=None):
    cur.execute('SELECT name, fields FROM page_configs WHERE id = %s', (f'page-{collection}',))
    pc = cur.fetchone()
    page_name = pc[0] if pc else collection
    fields = pc[1] if pc else []
    if record_id:
        cur.execute('SELECT id, data, created_at FROM dynamic_data '
                    'WHERE collection = %s AND id = %s AND branch_id = %s',
                    (collection, record_id, branch_id))
    else:
        cur.execute('SELECT id, data, created_at FROM dynamic_data '
                    'WHERE collection = %s AND branch_id = %s ORDER BY created_at',
                    (collection, branch_id))
    data = []
    for r in cur.fetchall():
        rec = {'id': r[0]}
        if r[1]:
            rec.update(r[1])
        if r[2] and hasattr(r[2], 'astimezone'):
            rec['createdAt'] = r[2].astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        data.append(rec)
    return page_name, fields, data


def execute_bound_export(cur, script_row, *, collection=None, menu_id=None,
                         branch_id='main', role=None, record_id=None):
    """校验绑定 + RBAC，取数解引用跑沙箱。

    page 维度返回 (bytes, filename, content_type)；menu 维度返回 list[(bytes, filename, content_type)]。
    """
    s = _script_fields(script_row)
    check_binding(script_row, collection=collection, menu_id=menu_id)

    if s['scope'] == 'menu':
        check_rbac(cur, menu_id=menu_id, role=role)
        collections = get_menu_collections(cur, menu_id)
        menu_data = []
        for coll in collections:
            page_name, fields, data = _fetch_page_data(cur, coll, branch_id)
            menu_data.append({'collection': coll, 'pageName': page_name, 'records': data,
                              'fields': fields, 'recordCount': len(data)})
        try:
            refs = resolve_references(cur, menu_data, export_branch=branch_id)
        except Exception:
            refs = {}
        return run_menu_export_script(s['script'], menu_data, menu_id, s['output_format'], references=refs)

    # page / row 维度
    check_rbac(cur, collection=collection, role=role)
    page_name, fields, data = _fetch_page_data(cur, collection, branch_id, record_id=record_id)
    try:
        refs = resolve_page_references(cur, collection, data, fields, export_branch=branch_id)
    except Exception:
        refs = {}
    return run_export_script(s['script'], data, fields, page_name, s['output_format'], references=refs)
