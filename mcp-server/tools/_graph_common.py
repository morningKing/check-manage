"""数据页关联关系图查询的共享助手(供 graph_neighbors / graph_traverse 使用)。

数据模型:data_relations 是有向边表
  (collection, record_id, field_name) → (related_collection, related_id, branch_id)
天然构成属性图;related_id 侧建有反向索引,反向遍历走索引。

可见性:与 list_collections 同口径——menus JOIN page_configs 后按当前角色过滤,
图遍历只在可见集合间扩展。
"""
import os

from db import get_db


def visible_collections(cur, role: str) -> set:
    """当前角色可见的数据页集合(admin 全量),与 list_collections 同口径。"""
    cur.execute(
        """
        SELECT m.page_id, m.roles
        FROM menus m
        JOIN page_configs pc ON pc.id = m.page_id
        WHERE m.page_id IS NOT NULL
        """
    )
    out = set()
    for page_id, roles in cur.fetchall():
        collection = page_id[5:] if page_id.startswith('page-') else page_id
        if role == 'admin' or (roles and role in roles):
            out.add(collection)
    return out


def normalize_direction(raw) -> str:
    d = (raw or 'both').strip().lower()
    return d if d in ('both', 'out', 'in') else 'both'


def expand_frontier(cur, pairs, direction, rel_fields):
    """从 frontier(一组 (collection, record_id))出发取一跳边。

    返回归一化边列表 [{fromCollection, fromId, field, toCollection, toId,
    branch}]:from 恒为 frontier 侧,to 为对侧——调用方无需再判断方向。
    已按 frontier 精确过滤(不依赖 SQL 端组合类型匹配)。"""
    if not pairs:
        return []
    pair_set = set(pairs)
    ids = [rid for _, rid in pairs]
    out = []

    def rows(sql, params):
        cur.execute(sql, params)
        return cur.fetchall()

    if direction in ('out', 'both'):
        for coll, rid, fname, rcoll, rid2, branch in rows(
            "SELECT collection, record_id, field_name, related_collection, "
            "related_id, branch_id FROM data_relations WHERE record_id = ANY(%s)",
            (ids,),
        ):
            if (coll, rid) not in pair_set:
                continue
            out.append({'fromCollection': coll, 'fromId': rid, 'field': fname,
                        'toCollection': rcoll, 'toId': rid2, 'branch': branch})
    if direction in ('in', 'both'):
        for rcoll, rid2, fname, coll, rid, branch in rows(
            "SELECT related_collection, related_id, field_name, "
            "collection, record_id, branch_id FROM data_relations "
            "WHERE related_id = ANY(%s)",
            (ids,),
        ):
            if (rcoll, rid2) not in pair_set:
                continue
            out.append({'fromCollection': rcoll, 'fromId': rid2, 'field': fname,
                        'toCollection': coll, 'toId': rid, 'branch': branch})

    if rel_fields:
        allowed = set(rel_fields)
        out = [e for e in out if e['field'] in allowed]
    # 去重(同一物理边可能经出/入两个方向各取一次)
    dedup = {(e['fromCollection'], e['fromId'], e['field'],
              e['toCollection'], e['toId']): e for e in out}
    return list(dedup.values())


def other_end(edge, collection, record_id):
    """边相对中心记录的另一端(中心可能在 from 或 to)。"""
    if (edge.get('fromCollection'), edge.get('fromId')) == (collection, record_id):
        return edge.get('toCollection'), edge.get('toId')
    return edge.get('fromCollection'), edge.get('fromId')


def load_node_pairs(pairs, get_db=None):
    """批量水合 (collection, id) 对的记录数据;缺失的节点数据为 None。"""
    get = get_db or __import__('db').get_db
    out = {}
    if not pairs:
        return out
    cols = list({c for c, _ in pairs})
    ids = [i for _, i in pairs]
    with get() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, collection, data FROM dynamic_data "
                "WHERE id = ANY(%s) AND collection = ANY(%s)",
                (ids, cols),
            )
            for rid, coll, data in cur.fetchall():
                out[(coll, rid)] = data
    return out
