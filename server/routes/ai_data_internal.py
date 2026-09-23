"""Internal data-write forwarding for the MCP server (NOT for browsers).

AI 会话里的数据写操作（建记录/改记录/删记录/挂数据菜单）此前只能裸 SQL 直写
dynamic_data/menus——绕过字段校验脚本、主键查重、autoSequence 原子分配、
工作流状态机、乐观锁、跨集合引用删除保护、触发器/Webhook 和操作日志。本端点
让 MCP 把写请求转发回**真实的应用层路由**（routes/dynamic.py、routes/menus.py），
由 Flask 以该会话用户的身份完整执行：权限（require_page_action/admin.menus）、
校验、副作用与审计与 UI 操作完全同路。

Guarded by the shared MCP_INTERNAL_TOKEN (同 ai_memory_internal)。转发范围按
白名单收紧——只有动态数据单条写与数据菜单创建，其它任何路径/方法一律 403；
不提供 GET（MCP 侧读取走 query_collection 直连 DB 的既有通道）。
"""
import re

from flask import Blueprint, current_app, jsonify, request

from auth import create_token
from config import MCP_INTERNAL_TOKEN
from db import get_db

ai_data_internal_bp = Blueprint('ai_data_internal', __name__,
                                url_prefix='/ai/data-internal')

_SEG = r'[A-Za-z0-9][A-Za-z0-9_-]*'
# (method, pattern) — 动态数据单条创建/更新/删除 + 数据菜单创建
_ALLOWED = (
    ('POST', re.compile(rf'^/{_SEG}$')),
    ('PUT', re.compile(rf'^/{_SEG}/{_SEG}$')),
    ('DELETE', re.compile(rf'^/{_SEG}/{_SEG}$')),
    ('POST', re.compile(r'^/menus$')),
)


def _authorized() -> bool:
    token = request.headers.get('X-Internal-Token', '')
    return bool(MCP_INTERNAL_TOKEN) and token == MCP_INTERNAL_TOKEN


def _forward_allowed(method: str, path: str):
    for m, pat in _ALLOWED:
        if m == method and pat.match(path):
            if path == '/menus':
                return True
            # 动态数据写只允许真实数据集合:保留路径(users/menus/pageConfigs/
            # ai/...)一律拒绝,不能借道系统路由
            from routes.dynamic import RESERVED
            if path.strip('/').split('/')[0] in RESERVED:
                return False
            return True
    return False


@ai_data_internal_bp.route('/execute', methods=['POST'])
def internal_execute():
    """{userId, method, path, body} → 以该用户身份转发到真实应用层路由。

    返回 {'status': <http code>, 'body': <json 或文本>}。4xx/5xx 原样透传，
    让调用方（MCP 工具）拿到路由本身的友好错误（校验失败/主键冲突/权限不足/
    引用保护等），而不是在这里吞掉语义。
    """
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    payload = request.get_json(silent=True) or {}
    user_id = (payload.get('userId') or '').strip()
    method = (payload.get('method') or '').upper().strip()
    path = (payload.get('path') or '').strip()
    body = payload.get('body')
    if not user_id or not path:
        return jsonify({'error': 'userId 与 path 必填'}), 400
    if method not in ('POST', 'PUT', 'DELETE'):
        return jsonify({'error': f'不允许的方法 {method}'}), 403
    if not path.startswith('/') or '?' in path or '..' in path:
        return jsonify({'error': 'path 非法'}), 400
    if not _forward_allowed(method, path):
        return jsonify({'error': f'转发白名单不允许 {method} {path}'}), 403

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, role FROM users WHERE id = %s",
                (user_id,))
            row = cur.fetchone()
    if not row:
        return jsonify({'error': f'用户不存在: {user_id}'}), 403
    token = create_token({'id': row[0], 'username': row[1], 'role': row[2]})

    # 进程内 WSGI 转发（非回环网络请求）：动态数据路由是 Flask 视图函数内联
    # 逻辑（request/g 耦合），test_client 是复用它们的唯一不动现有代码的方式。
    # 每次调用独立请求上下文，与并发请求互不干扰。
    resp = current_app._get_current_object().test_client().open(
        path, method=method, json=body if body is not None else None,
        headers={'Authorization': f'Bearer {token}'})
    parsed = resp.get_json(silent=True)
    return jsonify({'status': resp.status_code,
                    'body': parsed if parsed is not None
                    else resp.get_data(as_text=True)[:2000]})
