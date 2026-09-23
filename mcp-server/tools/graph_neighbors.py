"""Tool: graph_neighbors — 数据页关联关系的 1 跳邻域查询(知识图谱式)。

给一个记录 (collection, record_id),返回它的关联边与邻居节点:
- 方向 both/out/in;out=它指向谁,in=谁指向它;
- 可按关系字段(field_name)与目标集合过滤;
- 邻居节点批量水合返回完整数据。

数据模型与权限见 tools/_graph_common.py(只在当前角色可见的集合间查询)。
"""
import mcp.types as types

from db import get_db
from context import ToolContext
from tools._graph_common import (
    visible_collections, normalize_direction, expand_frontier, load_node_pairs,
)

NAME = "graph_neighbors"

TOOL = types.Tool(
    name=NAME,
    description=(
        "查询某个数据页记录的关联关系邻域(1 跳,知识图谱式):返回它直接关联的"
        "边与邻居记录的完整数据。方向 both/out/in 可选;可按关系字段与目标集合过滤。"
        "数据页与记录必须存在;只在当前角色可见的集合间查询。"
        "需要多跳遍历时改用 graph_traverse。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "collection": {"type": "string", "description": "记录所在数据页的集合标识"},
            "record_id": {"type": "string", "description": "记录 ID"},
            "direction": {"type": "string", "enum": ["both", "out", "in"],
                          "description": "out=该记录指向谁;in=谁指向该记录;默认 both"},
            "rel_fields": {"type": "array", "items": {"type": "string"},
                           "description": "只看这些关系字段(field_name),如 [\"link\"]"},
            "collections": {"type": "array", "items": {"type": "string"},
                            "description": "只保留邻居落在这些集合里的边(可选)"},
        },
        "required": ["collection", "record_id"],
        "additionalProperties": False,
    },
)


class GraphError(Exception):
    pass


def other_end(edge, collection, record_id):
    """边相对中心记录的另一端(中心可能在 from 或 to)。"""
    if edge['fromCollection'] == collection and edge['fromId'] == record_id:
        return edge['toCollection'], edge['toId']
    return edge['fromCollection'], edge['fromId']


def handle(input: dict, ctx: ToolContext) -> dict:
    inp = input or {}
    collection = (inp.get("collection") or "").strip()
    record_id = (inp.get("record_id") or "").strip()
    if not collection or not record_id:
        raise GraphError("collection 与 record_id 必填")

    direction = normalize_direction(inp.get("direction"))
    rel_fields = inp.get("rel_fields") or None
    collections_filter = inp.get("collections") or None

    with get_db() as conn:
        cur = conn.cursor()
        visible = visible_collections(cur, ctx.role)
        if collection not in visible:
            raise GraphError(f"数据页 {collection} 不存在或对当前角色不可见")
        cur.execute(
            "SELECT data FROM dynamic_data WHERE id = %s AND collection = %s",
            (record_id, collection),
        )
        row = cur.fetchone()
        if row is None:
            raise GraphError(f"记录不存在:{collection}/{record_id}")
        center_data = row[0] or {}

        edges = expand_frontier(cur, [(collection, record_id)], direction, rel_fields or None)

    kept, nodes = [], []
    seen = set()
    for e in edges:
        ocol, oid = e['toCollection'], e['toId']
        if ocol not in visible:
            continue
        if collections_filter and ocol not in collections_filter:
            continue
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT data FROM dynamic_data WHERE id = %s AND collection = %s",
                (oid, ocol),
            )
            r = cur.fetchone()
        kept.append({**e, 'direction': 'out' if (e['fromCollection'], e['fromId']) == (collection, record_id)
                     else 'in'})
        nodes.append({'collection': ocol, 'id': oid, 'data': r[0] if r else None})
        seen.add((ocol, oid))

    return {
        'center': {'collection': collection, 'id': record_id, 'data': center_data},
        'direction': direction,
        'edges': kept,
        'neighbors': nodes,
        'edgeCount': len(kept),
    }
