"""Shared transport for the platform data-write tools (create/update/delete
records, attach menu)。

写能力不直连 DB：MCP 把请求交给 Flask 的内部转发端点
（POST /ai/data-internal/execute，X-Internal-Token 鉴权，见
server/routes/ai_data_internal.py），由服务端以该会话用户身份走**真实应用层
路由**——字段校验脚本、主键查重、autoSequence 原子分配、工作流状态机、
跨集合引用删除保护、触发器/Webhook、操作日志全部生效。此前 AI 在会话里
"构造数据写表/挂菜单"只能裸 SQL 写 dynamic_data/menus，绕过以上全部语义，
这正是这组工具要堵上的洞。

Stdlib + requests（mcp 依赖已有 requests，见 pyproject）。
"""

import os

import requests

from context import ToolContext
from rbac import is_readonly


class DataApiError(Exception):
    """应用层路由返回的非 2xx——message 是可直接给模型看的路由错误。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _backend_base() -> str:
    return os.getenv('FLASK_INTERNAL_URL', 'http://127.0.0.1:3002')


def _internal_token() -> str:
    return (os.getenv('MCP_INTERNAL_TOKEN', '') or '').strip()


def ensure_writable(ctx: ToolContext, tool_name: str):
    """公开只读身份（guest / kefu-guest）一律拒绝写工具。路由层的
    require_page_action 还会按页面配置再拦一次，这里是第一道、给出明确话术。"""
    if is_readonly(ctx.role):
        raise PermissionError(f"{tool_name} 是写操作，只读身份（{ctx.role}）不可调用")


def resolve_collection(cur, identifier: str):
    """菜单名/集合标识 → 集合 slug。数据页还没挂菜单时（正是挂菜单工具的
    前置场景）回退 page_configs 直查（page id / api_endpoint）。"""
    from collection_resolve import resolve_collection as _menu_resolve
    slug = _menu_resolve(cur, identifier)
    if slug:
        return slug
    cur.execute(
        "SELECT id FROM page_configs WHERE id = %s OR api_endpoint = %s LIMIT 1",
        ('page-' + identifier, '/' + identifier))
    row = cur.fetchone()
    if not row:
        return None
    page_id = row[0] or ''
    return page_id[5:] if page_id.startswith('page-') else page_id


def execute(ctx: ToolContext, method: str, path: str, body=None) -> dict:
    """转发一次写请求，返回路由的 JSON 响应；非 2xx 抛 DataApiError。"""
    resp = requests.post(
        _backend_base() + '/ai/data-internal/execute',
        json={'userId': ctx.user_id, 'method': method, 'path': path, 'body': body},
        headers={'X-Internal-Token': _internal_token(), 'Content-Type': 'application/json'},
        timeout=120,
    )
    if resp.status_code != 200:
        # 转发端点自身的错误（403 白名单/鉴权、400 参数）——不是路由的业务错误
        try:
            detail = resp.json().get('error', '')
        except ValueError:
            detail = ''
        raise DataApiError(resp.status_code, detail or f'内部转发失败({resp.status_code})')
    out = resp.json() or {}
    status = int(out.get('status', 500))
    body_out = out.get('body')
    if status >= 400:
        msg = body_out.get('error') if isinstance(body_out, dict) else None
        raise DataApiError(status, str(msg) if msg else f'路由返回 {status}')
    return body_out if isinstance(body_out, dict) else {}
