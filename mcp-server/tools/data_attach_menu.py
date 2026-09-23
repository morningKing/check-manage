"""Tool: data_attach_menu —— 把已有数据页挂到项目分组下的导航菜单
（应用层同路，仅管理员）。

此前 AI"挂菜单"只能裸 INSERT menus：绕过 menu_type 层级校验（工作空间→
项目→数据菜单三级树，数据菜单必须有项目父级）、数据页名称全局唯一约束的
友好提示、project 归属推导和操作日志。本工具走真实菜单创建路由
（POST /menus，admin.menus 权限），语义与菜单管理 UI 完全一致。

页定位：collection 标识 / 数据页名称 / pageId 均可（page_configs 直查，
不要求已有菜单——本工具的核心场景就是"建了页没挂菜单"）。父级：项目菜单
的名称或 id（必填，平台菜单树要求数据菜单挂在项目分组下）。
"""

import uuid

import mcp.types as types

from context import ToolContext
from db import get_db
from tools._data_api import DataApiError, ensure_writable, execute

NAME = "data_attach_menu"

TOOL = types.Tool(
    name=NAME,
    description=(
        "把一个已有数据页挂到左侧导航的项目分组下（等价菜单管理里的"
        "「新增数据页菜单」，仅管理员；**不要再用 SQL 直写 menus**）。适用于"
        "建表后没挂菜单、或要把页面挂到别的项目分组的场景。"
        "参数：collection=集合标识或数据页名称（必填，如 inspection-case 或"
        "「巡检记录」）；parent=父级项目分组菜单的名称或 id（必填，菜单树要求"
        "工作空间→项目→数据菜单三级）；menu_name=菜单显示名（可选，默认页面名）；"
        "menu_roles=可见角色数组（可选，默认 admin/developer/guest）；"
        "icon=图标名（可选，默认 Document）。菜单名与已有数据页重名时会报错，"
        "换 menu_name 重试。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "collection": {"type": "string", "description": "集合标识或数据页名称"},
            "menu_name": {"type": "string", "description": "菜单显示名，默认页面名"},
            "parent": {"type": "string", "description": "父级项目分组菜单名称或 id（必填）"},
            "menu_roles": {"type": "array", "items": {"type": "string"},
                           "description": "可见角色，默认 admin/developer/guest"},
            "icon": {"type": "string", "description": "图标名，默认 Document"},
        },
        "required": ["collection", "parent"],
        "additionalProperties": False,
    },
)


class DataMenuError(Exception):
    pass


def _require_admin(ctx: ToolContext):
    if ctx.role != 'admin':
        raise PermissionError(
            f"data_attach_menu 仅管理员可用(当前角色 {ctx.role})；菜单结构变更"
            "与菜单管理 UI 同权限(admin.menus)")


def handle(input: dict, ctx: ToolContext) -> dict:
    _require_admin(ctx)
    ensure_writable(ctx, NAME)
    inp = input or {}
    identifier = (inp.get("collection") or "").strip()
    if not identifier:
        raise DataMenuError("collection 必填：集合标识或数据页名称")
    parent_id = (inp.get("parent") or '').strip()
    if not parent_id:
        raise DataMenuError(
            "parent 必填：菜单树要求数据菜单挂在项目分组下（工作空间→项目→"
            "数据菜单），传父级项目菜单的名称或 id")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, api_endpoint FROM page_configs "
                "WHERE id = %s OR api_endpoint = %s OR name = %s LIMIT 1",
                (identifier, '/' + identifier, identifier))
            page = cur.fetchone()
            if not page:
                raise DataMenuError(
                    f"数据页「{identifier}」不存在(page_configs 无此页)："
                    "先用 list_collections 确认可用集合，或先用 ai_create_data_page 建页")
            page_id, page_name, endpoint = page[0], page[1], page[2] or ''

            cur.execute(
                "SELECT id FROM menus WHERE (id = %s OR name = %s) "
                "AND menu_type = 'project' LIMIT 1",
                (parent_id, parent_id))
            prow = cur.fetchone()
            if not prow:
                raise DataMenuError(
                    f"父级项目菜单「{parent_id}」不存在或不是项目类型："
                    "parent 需要项目分组菜单的名称或 id")
            parent_id = prow[0]

    menu_name = (inp.get("menu_name") or '').strip() or page_name
    roles = inp.get("menu_roles") or ['admin', 'developer', 'guest']
    if not isinstance(roles, list) or not all(isinstance(r, str) and r for r in roles):
        raise DataMenuError("menu_roles 必须是字符串数组")

    body = {
        # menus.id 无默认值,菜单管理 UI 由前端生成 uuid——工具侧同样生成
        'id': str(uuid.uuid4()),
        'name': menu_name,
        'menuType': 'data',
        'pageId': page_id,
        'path': endpoint or '/' + page_id.replace('page-', '', 1),
        'roles': roles,
        'icon': (inp.get("icon") or 'Document').strip() or 'Document',
    }
    if parent_id:
        body['parentId'] = parent_id
    try:
        execute(ctx, 'POST', '/menus', body)
    except DataApiError as e:
        raise DataMenuError(f"挂菜单失败：{e.message}")

    return {
        "attached": True,
        "menu": {"name": menu_name, "path": body['path'], "roles": roles,
                 "parentId": parent_id or None, "pageId": page_id},
        "note": "已走菜单管理真实路由：唯一性校验/类型校验/操作日志均已生效",
    }
