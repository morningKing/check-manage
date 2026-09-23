"""graph_neighbors / graph_traverse 真库测试(共享开发库,用后即清)。

种子:三个数据页 A/B/C 与关系边 A1 -link→ B1 -link→ C1(主链两跳)、
A2 -other→ C1(无关字段边)。页面进 menus+page_configs 以满足可见性。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db                              # noqa: E402
from context import ToolContext                    # noqa: E402
from tools import graph_neighbors, graph_traverse  # noqa: E402


@pytest.fixture(scope='module')
def graph_env():
    suffix = uuid.uuid4().hex[:8]
    cols = {'A': f'gq-a-{suffix}', 'B': f'gq-b-{suffix}', 'C': f'gq-c-{suffix}'}
    recs = {'A1': (cols['A'], 'A1'), 'A2': (cols['A'], 'A2'),
            'B1': (cols['B'], 'B1'), 'C1': (cols['C'], 'C1')}
    edges = [(cols['A'], 'A1', 'link', cols['B'], 'B1'),
             (cols['B'], 'B1', 'link', cols['C'], 'C1'),
             (cols['A'], 'A2', 'other', cols['C'], 'C1')]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE id='user-admin'")
            if cur.fetchone() is None:
                cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                            "VALUES ('user-admin','admin','x','管理员','admin')")
            for c in cols.values():
                cur.execute("INSERT INTO page_configs (id, name, fields) "
                            "VALUES (%s,%s,'[]'::jsonb) "
                            "ON CONFLICT (id) DO NOTHING", (f'page-{c}', c))
                cur.execute("INSERT INTO menus (id, name, page_id, menu_type, roles, path) "
                            "VALUES (%s,%s,%s,'data','[\"admin\",\"developer\"]',%s) "
                            "ON CONFLICT (id) DO NOTHING",
                            (f'menu-{c}', c, f'page-{c}', f'/{c}'))
            for key, (col, rid) in recs.items():
                cur.execute("INSERT INTO dynamic_data (id, collection, data) "
                            "VALUES (%s,%s,'{}'::jsonb)",
                            (rid, col))
            for src_c, src_id, fname, dst_c, dst_id in edges:
                cur.execute("INSERT INTO data_relations (collection, record_id, field_name, "
                            "related_collection, related_id) VALUES (%s,%s,%s,%s,%s) "
                            "ON CONFLICT DO NOTHING",
                            (src_c, src_id, fname, dst_c, dst_id))
    conn.commit()
    yield {'cols': cols, 'recs': recs, 'edges': edges}
    with get_db() as conn:
        with conn.cursor() as cur:
            for c in cols.values():
                cur.execute("DELETE FROM data_relations WHERE collection=%s "
                            "OR related_collection=%s", (c, c))
                cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (c,))
                cur.execute("DELETE FROM menus WHERE page_id=%s", (f'page-{c}',))
                cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{c}',))
    conn.commit()


def _ctx(role='admin'):
    from context import ToolContext
    return ToolContext(session_id='sess-test', user_id='user-admin', role=role)


def test_neighbors_out_finds_direct_successor(graph_env):
    res = graph_neighbors.handle(
        {'collection': graph_env['cols']['A'], 'record_id': 'A1'}, _ctx())
    assert res['edgeCount'] == 1
    assert res['edges'][0]['field'] == 'link'
    assert res['neighbors'][0]['id'] == 'B1'


def test_neighbors_in_on_b1_finds_a1(graph_env):
    res = graph_neighbors.handle(
        {'collection': graph_env['cols']['B'], 'record_id': 'B1',
         'direction': 'in'}, _ctx())
    assert res['edgeCount'] == 1
    assert res['neighbors'][0]['id'] == 'A1'


def test_neighbors_rel_fields_filter(graph_env):
    """A2 -other→ C1:按 other 字段过滤才可见。"""
    res = graph_neighbors.handle(
        {'collection': graph_env['cols']['A'], 'record_id': 'A2',
         'rel_fields': ['other']}, _ctx())
    assert res['edgeCount'] == 1
    assert res['neighbors'][0]['id'] == 'C1'
    # 过滤到 link 字段 → 无边
    res2 = graph_neighbors.handle(
        {'collection': graph_env['cols']['A'], 'record_id': 'A2',
         'rel_fields': ['link']}, _ctx())
    assert res2['edgeCount'] == 0


def test_traverse_two_hops_reaches_c1(graph_env):
    res = graph_traverse.handle(
        {'collection': graph_env['cols']['A'], 'record_id': 'A1',
         'max_depth': 2}, _ctx())
    ids = {n['id']: n['depth'] for n in res['nodes']}
    assert ids.get('B1') == 1 and ids.get('C1') == 2
    assert res['edgeCount'] == 2


def test_traverse_cycle_terminates(graph_env):
    """加一条 C1→A1 的反向边构成环,遍历必须终止且不重复出节点。"""
    f = graph_env
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO data_relations (collection, record_id, field_name, "
                        "related_collection, related_id) VALUES (%s,%s,'back',%s,%s) "
                        "ON CONFLICT DO NOTHING",
                        (f['cols']['C'], 'C1', f['cols']['A'], 'A1'))
    conn.commit()
    try:
        res = graph_traverse.handle(
            {'collection': f['cols']['A'], 'record_id': 'A1', 'max_depth': 4}, _ctx())
        # both 方向经 C1 的反向边(other→C1)可发现 A2:4 节点、环被 visited 跳过
        assert res['nodeCount'] == 4
        assert res['truncated'] is False
        assert len({n['id'] for n in res['nodes']}) == len(res['nodes'])
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM data_relations WHERE collection=%s "
                            "AND record_id=%s AND field_name='back'",
                            (f['cols']['C'], 'C1'))
            conn.commit()


def test_traverse_node_budget_truncates(graph_env):
    res = graph_traverse.handle(
        {'collection': graph_env['cols']['A'], 'record_id': 'A1',
         'max_depth': 2, 'max_nodes': 2}, _ctx())
    assert res['truncated'] is True
    assert res['nodeCount'] <= 2


def test_missing_record_raises(graph_env):
    with pytest.raises(Exception, match='记录不存在'):
        graph_neighbors.handle(
            {'collection': graph_env['cols']['A'], 'record_id': 'nope'}, _ctx())


def test_invisible_collection_raises(graph_env):
    """guest 角色不在测试页面的菜单角色里 → 数据页不可见。"""
    with pytest.raises(Exception, match='不可见'):
        graph_neighbors.handle(
            {'collection': graph_env['cols']['A'], 'record_id': 'A1'},
            _ctx('guest'))


def test_kefu_guest_blocked_by_allowlist():
    from rbac import tool_allowed
    assert not tool_allowed('graph_neighbors', 'kefu-guest')
    assert not tool_allowed('graph_traverse', 'kefu-guest')
    assert tool_allowed('graph_neighbors', 'admin')
