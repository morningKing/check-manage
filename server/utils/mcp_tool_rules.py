# -*- coding: utf-8 -*-
"""内置 MCP 工具治理：矩阵读取/全量替换（Flask 管理端点的数据层）。

矩阵语义见 spec §4-§5：全量行、无例外式隐式语义；行缺失时视图端用代码
默认（admin/developer=True，guest/kefu-guest=False）补齐展示。替换为事务内
DELETE + bulk INSERT，非法载荷整体不落库，返回 diff 摘要供操作日志。"""
from psycopg2.extras import RealDictCursor

from db import get_db

# 与 init_db 内置角色种子对齐（动态自建角色不进矩阵——spec §6）
MCP_TOOL_ROLES = ('admin', 'developer', 'guest', 'kefu-guest')

ROLE_LABELS = {'admin': '管理员', 'developer': '开发', 'guest': '访客', 'kefu-guest': '客服'}

DEFAULT_ALLOWED = {'admin': True, 'developer': True, 'guest': False, 'kefu-guest': False}


class ToolRulesError(ValueError):
    """非法矩阵载荷（未知角色/工具、缺字段、allowed 非布尔、重复行）。"""


def list_tool_governance():
    """分组工具清单 + 矩阵现状（快照表 join 矩阵，行缺失回默认值）。"""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT tool_name, group_id, group_name, group_order, description, access "
                "FROM ai_mcp_tools ORDER BY group_order, group_id, tool_name")
            tools = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT role, tool_name, allowed FROM ai_mcp_tool_rules")
            rows = cur.fetchall()
    rules = {(r['role'], r['tool_name']): r['allowed'] for r in rows}
    groups = {}
    for t in tools:
        g = groups.setdefault(t['group_id'], {'id': t['group_id'], 'name': t['group_name'],
                                              'order': t['group_order'], 'tools': []})
        g['tools'].append({
            'name': t['tool_name'], 'description': t['description'],
            'access': t['access'],
            'rules': {role: rules.get((role, t['tool_name']), DEFAULT_ALLOWED[role])
                      for role in MCP_TOOL_ROLES},
        })
    ordered = sorted(groups.values(), key=lambda g: (g['order'], g['id']))
    return {'groups': ordered,
            'roles': [{'role': r, 'label': ROLE_LABELS[r]} for r in MCP_TOOL_ROLES]}


def replace_tool_rules(rules):
    """全量替换矩阵。rules: [{role, tool_name, allowed}]；未知角色/工具、缺字段、
    allowed 非布尔、重复行 → ToolRulesError（整体不落库）。返回 diff 摘要。"""
    if not isinstance(rules, list) or not rules:
        raise ToolRulesError('rules must be a non-empty list')
    seen = set()
    for item in rules:
        if not isinstance(item, dict):
            raise ToolRulesError('each rule must be an object')
        role, tool, allowed = item.get('role'), item.get('tool_name'), item.get('allowed')
        if role not in MCP_TOOL_ROLES:
            raise ToolRulesError(f'unknown role: {role!r}')
        if not isinstance(tool, str) or not tool:
            raise ToolRulesError(f'bad tool_name: {tool!r}')
        if not isinstance(allowed, bool):
            raise ToolRulesError(f'allowed must be boolean: {allowed!r}')
        if (role, tool) in seen:
            raise ToolRulesError(f'duplicate rule: {role}/{tool}')
        seen.add((role, tool))
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT tool_name FROM ai_mcp_tools")
            known = {r['tool_name'] for r in cur.fetchall()}
            unknown = {t for _, t in seen if t not in known}
            if unknown:
                raise ToolRulesError(
                    'unknown tools (mcp-server 未同步快照?): ' + ', '.join(sorted(unknown)))
            cur.execute("SELECT role, tool_name, allowed FROM ai_mcp_tool_rules")
            old = {(r['role'], r['tool_name']): r['allowed'] for r in cur.fetchall()}
            new = {(i['role'], i['tool_name']): i['allowed'] for i in rules}
            diff = [f'{role}:{tool} {"on" if v else "off"}'
                    for (role, tool), v in sorted(new.items())
                    if old.get((role, tool), DEFAULT_ALLOWED[role]) != v]
            cur.execute("DELETE FROM ai_mcp_tool_rules")
            cur.executemany(
                "INSERT INTO ai_mcp_tool_rules (role, tool_name, allowed, updated_at) "
                "VALUES (%s, %s, %s, NOW())",
                [(i['role'], i['tool_name'], i['allowed']) for i in rules])
        conn.commit()
    summary = '无变更' if not diff else '; '.join(diff[:10]) + ('…' if len(diff) > 10 else '')
    return {'updated': len(diff), 'diff': summary}
