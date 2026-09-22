"""Tool: ai_create_data_page —— 将页面配置的"AI 建表"能力封装为内置工具。

一句话自然语言描述业务实体 → LLM 起草页面草案(名称/集合/菜单/字段)→
本工具直接落库:创建 page_configs + 挂数据菜单,一步到位。此前 AI 只出草案
(POST /pageConfigs/ai-draft),真正创建必须由管理员在页面配置对话框逐项确认;
封装后 agent 可在对话里直接建表。

草案复用服务端唯一实现 `utils.ai_schema_designer.draft_page_schema`(同一
ai_settings、同一安全控制类型白名单与归一化),运行时把仓库内的 server/
目录加入 sys.path——mcp-server 本就依赖同级的 server/.env(见 app_config),
目录约定一致。ai_schema_designer 需要 requests,已列入本包依赖。

权限:仅 admin。等价于 UI 的 require_permission('admin.page_configs');
建表改变平台结构,自定义角色与 kefu-guest 一律拒绝。

说明:字段索引优化(sync_field_indexes)属 server 侧能力,MCP 进程不重建;
如需字段索引,可在页面配置中重新保存一次。菜单挂在顶级(不选项目分组)。
"""

import json
import re
import sys
import time
import uuid
from pathlib import Path

import mcp.types as types
import psycopg2.extras

from db import get_db
from context import ToolContext

NAME = "ai_create_data_page"

# 仓库内 server/ 目录(mcp-server 的同级目录,app_config 已依赖该约定)
_SERVER_DIR = Path(__file__).resolve().parents[2] / 'server'
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

SLUG_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
DEFAULT_ROLES = ['admin', 'developer', 'guest']


TOOL = types.Tool(
    name=NAME,
    description=(
        "AI 建表:用一句自然语言描述业务实体(如「我要创建一张订货表,包含"
        "订单号、客户、数量、下单日期、状态」),AI 起草页面字段结构并直接"
        "创建数据页(含数据菜单),创建后即可在页面列表使用并写入数据。"
        "仅管理员可用。"
        "参数:description=业务描述(必填);collection_slug=集合标识覆盖"
        "(可选,小写字母数字连字符);menu_name=菜单名覆盖(可选);"
        "menu_roles=菜单可见角色数组(可选,默认 admin/developer/guest)。"
        "集合标识或菜单名被占用时,错误信息里会给建议,换一个名称重试即可。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "业务实体的自然语言描述,包含实体名称与需要的字段",
            },
            "collection_slug": {"type": "string",
                                "description": "覆盖 AI 起草的集合标识(可选)"},
            "menu_name": {"type": "string",
                          "description": "覆盖 AI 起草的菜单名(可选)"},
            "menu_roles": {"type": "array", "items": {"type": "string"},
                           "description": "菜单可见角色,默认 admin/developer/guest"},
        },
        "required": ["description"],
        "additionalProperties": False,
    },
)


class AiCreateDataPageError(Exception):
    pass


def _require_admin(ctx: ToolContext):
    if ctx.role != 'admin':
        raise PermissionError(
            f"ai_create_data_page 仅管理员可用(当前角色 {ctx.role});"
            "数据页创建会改变平台结构,不做宽授权")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _slug_ok(slug: str) -> bool:
    return bool(SLUG_RE.match(slug or ''))


def _field_configs(fields: list, stamp: str) -> list:
    """LLM 草案字段 → 页面配置字段(补 id/order,autoSequence 兜底配置),
    与前端 AiPageDesignDialog.toFieldConfig 同形。"""
    out = []
    for i, f in enumerate(fields or []):
        f = dict(f)
        f['id'] = f"field-{stamp}-{i}"
        f['order'] = i + 1
        if f.get('controlType') == 'autoSequence' and not f.get('sequenceConfig'):
            f['sequenceConfig'] = {'prefix': '', 'max': 9999}
        out.append(f)
    return out


def handle(input: dict, ctx: ToolContext) -> dict:
    _require_admin(ctx)
    inp = input or {}
    description = (inp.get("description") or "").strip()
    if not description:
        raise AiCreateDataPageError(
            "description 不能为空:用一句自然语言描述要创建的业务实体,"
            "包含实体名称与需要的字段")

    # 1) AI 起草 —— 服务端唯一实现(测试在此处打桩)
    try:
        from utils.ai_schema_designer import draft_page_schema
    except ImportError as e:
        raise AiCreateDataPageError(
            f"服务端 ai_schema_designer 不可用(检查 server/ 目录与 requests 依赖): {e}")
    try:
        draft = draft_page_schema(description)
    except RuntimeError as e:
        raise AiCreateDataPageError(str(e))

    # 2) 解析名称/标识:显式覆盖 > AI 草案
    slug = (inp.get("collection_slug") or draft.get("collectionSlug") or "").strip()
    menu_name = (inp.get("menu_name") or draft.get("menuName") or "").strip()
    menu_path = (inp.get("menu_path") or draft.get("menuPath") or f"/{slug}").strip()
    page_name = (draft.get("name") or menu_name or slug).strip()
    page_desc = (draft.get("description") or description).strip()
    roles = inp.get("menu_roles") or DEFAULT_ROLES
    if not isinstance(roles, list) or not all(isinstance(r, str) and r for r in roles):
        raise AiCreateDataPageError("menu_roles 必须是字符串数组")
    if not _slug_ok(slug):
        raise AiCreateDataPageError(
            f"集合标识「{slug}」不合法:需为小写字母/数字/连字符(如 purchase-orders);"
            "可用 collection_slug 参数覆盖后重试")
    if not menu_name:
        raise AiCreateDataPageError("菜单名为空:AI 草案缺少 menuName,可用 menu_name 参数覆盖后重试")

    fields = _field_configs(draft.get("fields"), str(int(time.time() * 1000)))
    if not fields:
        raise AiCreateDataPageError("AI 草案没有可用字段,请补充描述(实体包含哪些字段)后重试")

    page_id = f'page-{slug}'
    stamp = _now_iso()
    with get_db() as conn:
        cur = conn.cursor()
        # 占用检查:给出友好错误而不是让唯一约束异常冒泡
        cur.execute("SELECT 1 FROM page_configs WHERE id = %s", (page_id,))
        if cur.fetchone():
            raise AiCreateDataPageError(
                f"集合标识「{slug}」已被占用(page_configs 已存在);"
                "换一个 collection_slug 或直接使用现有页面")
        cur.execute(
            "SELECT 1 FROM menus WHERE menu_type = 'data' AND name = %s",
            (menu_name,))
        if cur.fetchone():
            raise AiCreateDataPageError(
                f"数据页名称「{menu_name}」已被占用,换一个 menu_name 重试")

        # 3) 建页:与 POST /pageConfigs 同形(幂等字段由唯一约束兜底)
        cur.execute(
            'INSERT INTO page_configs '
            '  (id, name, description, api_endpoint, fields, created_at, updated_at, row_actions) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
            (page_id, page_name, page_desc, f'/{slug}',
             psycopg2.extras.Json(fields), stamp, stamp,
             psycopg2.extras.Json([])),
        )
        # 4) 挂数据菜单(顶级;roles 与 UI 默认一致)
        cur.execute(
            'INSERT INTO menus '
            '  (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
            'VALUES (%s,%s,%s,%s,NULL,%s,%s,%s,%s)',
            (str(uuid.uuid4()), menu_name, 'Document', page_id, 1, menu_path,
             json.dumps(roles), 'data'),
        )

    return {
        "created": True,
        "pageId": page_id,
        "collection": slug,
        "name": page_name,
        "description": page_desc,
        "menu": {"name": menu_name, "path": menu_path, "roles": roles},
        "fields": [{"fieldName": f.get("fieldName"), "label": f.get("label"),
                    "controlType": f.get("controlType"), "required": f.get("required")}
                   for f in fields],
        "fieldCount": len(fields),
        "note": ("页面与菜单已创建;如需字段索引优化,可在页面配置中重新保存一次;"
                 "删除请走页面配置管理,不要重复调用本工具"),
    }
