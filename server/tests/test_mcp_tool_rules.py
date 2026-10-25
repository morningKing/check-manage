# -*- coding: utf-8 -*-
"""utils/mcp_tool_rules 数据层测试：分组视图/全量替换/校验拒绝/行缺失回默认。

矩阵与快照是全局状态：夹具先快照现有矩阵行 → 清空 → 插入 mtg-test-% 假
工具行构造沙箱；teardown 恢复原矩阵并删假行（casemanage_test 也别留脏）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture(autouse=True)
def _matrix_sandbox(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT role, tool_name, allowed FROM ai_mcp_tool_rules")
        saved = cur.fetchall()
        cur.execute("DELETE FROM ai_mcp_tool_rules")
        cur.execute("DELETE FROM ai_mcp_tools WHERE tool_name LIKE 'mtg-test-%'")
        cur.executemany(
            "INSERT INTO ai_mcp_tools (tool_name, group_id, group_name, group_order, description, access) "
            "VALUES (%s, 'data-query', '数据查询', 1, '测试工具', %s)",
            [('mtg-test-a', 'read'), ('mtg-test-b', 'write')])
    db_conn.commit()
    yield
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_mcp_tool_rules")
        if saved:
            cur.executemany(
                "INSERT INTO ai_mcp_tool_rules (role, tool_name, allowed) VALUES (%s, %s, %s)",
                saved)
        cur.execute("DELETE FROM ai_mcp_tools WHERE tool_name LIKE 'mtg-test-%'")
    db_conn.commit()


def test_list_groups_shape_and_defaults():
    from utils.mcp_tool_rules import list_tool_governance
    out = list_tool_governance()
    by_tool = {t['name']: t for g in out['groups'] for t in g['tools']}
    assert set(by_tool) == {'mtg-test-a', 'mtg-test-b'}
    g = out['groups'][0]
    assert g['id'] == 'data-query' and g['name'] == '数据查询'
    # 矩阵为空 → 行缺失回代码默认（spec §4.3）：admin/developer True，受限角色 False
    assert by_tool['mtg-test-a']['rules'] == {
        'admin': True, 'developer': True, 'guest': False, 'kefu-guest': False}
    assert by_tool['mtg-test-b']['access'] == 'write'
    assert {'role': 'kefu-guest', 'label': '客服'} in out['roles']


def test_replace_rules_roundtrip_and_diff():
    from utils.mcp_tool_rules import list_tool_governance, replace_tool_rules
    full = [{'role': r, 'tool_name': t, 'allowed': a}
            for t in ('mtg-test-a', 'mtg-test-b')
            for r, a in (('admin', True), ('developer', False),
                         ('guest', True), ('kefu-guest', False))]
    res = replace_tool_rules(full)
    assert res['updated'] == 4  # developer×2 off + guest×2 on（其余等于默认）
    assert 'developer:mtg-test-a off' in res['diff']
    assert 'guest:mtg-test-b on' in res['diff']
    out = list_tool_governance()
    by_tool = {t['name']: t for g in out['groups'] for t in g['tools']}
    assert by_tool['mtg-test-a']['rules']['developer'] is False
    assert by_tool['mtg-test-a']['rules']['guest'] is True


def test_replace_rules_atomic_on_unknown_tool():
    from utils.mcp_tool_rules import replace_tool_rules, ToolRulesError, list_tool_governance
    good = [{'role': 'admin', 'tool_name': 'mtg-test-a', 'allowed': True}]
    bad = good + [{'role': 'admin', 'tool_name': 'no-such-tool', 'allowed': True}]
    with pytest.raises(ToolRulesError):
        replace_tool_rules(bad)
    # 整体回滚：good 行也没落库（仍是空矩阵→默认视图）
    out = list_tool_governance()
    t = [t for g in out['groups'] for t in g['tools'] if t['name'] == 'mtg-test-a'][0]
    assert t['rules']['developer'] is True


def test_replace_rules_validation_errors():
    from utils.mcp_tool_rules import replace_tool_rules, ToolRulesError
    for bad in (
        [],
        [{'role': 'hacker', 'tool_name': 'mtg-test-a', 'allowed': True}],
        [{'role': 'admin', 'tool_name': '', 'allowed': True}],
        [{'role': 'admin', 'tool_name': 'mtg-test-a', 'allowed': 'yes'}],
        [{'role': 'admin', 'tool_name': 'mtg-test-a', 'allowed': True},
         {'role': 'admin', 'tool_name': 'mtg-test-a', 'allowed': False}],  # 重复
        'not-a-list',
    ):
        with pytest.raises(ToolRulesError):
            replace_tool_rules(bad)
