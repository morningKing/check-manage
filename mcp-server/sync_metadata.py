# -*- coding: utf-8 -*-
"""启动时把 registry 元数据快照进 ai_mcp_tools，并播种矩阵默认行（spec §4.3）。

快照 = upsert 全量 + 删除注册集中已消失的行（工具改名/下架不留尾巴）；失败
由 main.py 记 warning 不阻断启动（快照只服务管理展示，执法走 rbac 默认语义）。
矩阵播种 ON CONFLICT DO NOTHING——永不覆盖管理员选择。"""
from db import get_db
from rbac import KEFU_TOOL_ALLOWLIST, ROLES
from tools import tool_meta


def _default_allowed(role: str, m: dict) -> bool:
    if role in ('admin', 'developer'):
        return True
    if role == 'guest':
        return m['access'] == 'read'
    if role == 'kefu-guest':
        return m['name'] in KEFU_TOOL_ALLOWLIST
    return False


def sync_metadata_to_db() -> dict:
    meta = tool_meta()
    names = [m['name'] for m in meta]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ai_mcp_tools (tool_name, group_id, group_name, group_order, "
                "description, access, updated_at) VALUES (%s,%s,%s,%s,%s,%s,NOW()) "
                "ON CONFLICT (tool_name) DO UPDATE SET group_id=EXCLUDED.group_id, "
                "group_name=EXCLUDED.group_name, group_order=EXCLUDED.group_order, "
                "description=EXCLUDED.description, access=EXCLUDED.access, "
                "updated_at=NOW()",
                [(m['name'], m['group'], m['group_name'], m['group_order'],
                  m['description'], m['access']) for m in meta])
            cur.execute("DELETE FROM ai_mcp_tools WHERE NOT (tool_name = ANY(%s))", (names,))
            cur.executemany(
                "INSERT INTO ai_mcp_tool_rules (role, tool_name, allowed, updated_at) "
                "VALUES (%s,%s,%s,NOW()) ON CONFLICT (role, tool_name) DO NOTHING",
                [(role, m['name'], _default_allowed(role, m))
                 for m in meta for role in ROLES])
        conn.commit()
    return {'tools': len(meta), 'rules_seeded': len(meta) * len(ROLES)}
