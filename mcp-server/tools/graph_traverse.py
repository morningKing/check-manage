"""Tool: graph_traverse — 数据页关联关系的多跳遍历(知识图谱式)。

从一个记录出发,沿关联边 BFS 遍历最多 max_depth 跳(默认 2,上限 5),
返回途经的边与每个到达节点的完整数据(含深度与路径)。环由 visited 集
自动跳过;节点数达到上限时标记 truncated 并停止扩展。

数据模型与权限见 tools/_graph_common.py(只在当前角色可见的集合间遍历)。
1 跳场景请用 graph_neighbors。
"""
import mcp.types as types

from db import get_db
from context import ToolContext
from tools._graph_common import (
    visible_collections, normalize_direction, expand_frontier, load_node_pairs,
)

NAME = "graph_traverse"

MAX_DEPTH = 5
DEFAULT_DEPTH = 2
DEFAULT_MAX_NODES = 200
MAX_NODES = 500

TOOL = types.Tool(
    name=NAME,
    description=(
        "多跳遍历某数据页记录的关联关系(知识图谱式):从一条记录出发,沿关联边"
        "最多走 max_depth 跳(默认 2),返回途经的边与每个到达节点的完整数据"
        "(含深度与路径)。环自动跳过;节点数达到上限时停止并标记 truncated。"
        "例:用例 → 所属模块 → 所属项目的两跳链路。"
        "1 跳场景请用 graph_neighbors。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "collection": {"type": "string", "description": "起点记录所在数据页的集合标识"},
            "record_id": {"type": "string", "description": "起点记录 ID"},
            "direction": {"type": "string", "enum": ["both", "out", "in"],
                          "description": "out=沿该记录指向的方向;in=反方向;默认 both"},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 5,
                          "description": "最大跳数,默认 2"},
            "max_nodes": {"type": "integer", "minimum": 1, "maximum": 500,
                          "description": "最多返回的节点数,默认 200"},
            "rel_fields": {"type": "array", "items": {"type": "string"},
                           "description": "只走这些关系字段(field_name)"},
            "collections": {"type": "array", "items": {"type": "string"},
                            "description": "只保留落在这些集合里的边(可选)"},
        },
        "required": ["collection", "record_id"],
        "additionalProperties": False,
    },
)


class GraphError(Exception):
    pass


def handle(input: dict, ctx: ToolContext) -> dict:
    inp = input or {}
    collection = (inp.get("collection") or "").strip()
    record_id = (inp.get("record_id") or "").strip()
    if not collection or not record_id:
        raise GraphError("collection 与 record_id 必填")

    direction = normalize_direction(inp.get("direction"))
    try:
        max_depth = min(max(int(inp.get("max_depth") or DEFAULT_DEPTH), 1), MAX_DEPTH)
    except (TypeError, ValueError):
        max_depth = DEFAULT_DEPTH
    try:
        max_nodes = min(max(int(inp.get("max_nodes") or DEFAULT_MAX_NODES), 1), MAX_NODES)
    except (TypeError, ValueError):
        max_nodes = DEFAULT_MAX_NODES
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

        start = (collection, record_id)
        # BFS:seen 记录已到达节点(环自动跳过);depth_of/parent 支撑深度与路径
        seen = {start}
        depth_of = {start: 0}
        parent = {}
        frontier = [start]
        edges = []
        truncated = False

        for depth in range(1, max_depth + 1):
            if not frontier or len(depth_of) >= max_nodes:
                if len(depth_of) >= max_nodes:
                    truncated = True
                break
            level_edges = expand_frontier(cur, frontier, direction, rel_fields or None)
            fresh = []
            for e in level_edges:
                other = (e['toCollection'], e['toId'])
                if other in seen:
                    continue
                if other[0] not in visible:
                    continue
                if collections_filter and other[0] not in collections_filter:
                    continue
                if len(depth_of) >= max_nodes:
                    truncated = True
                    continue
                seen.add(other)
                depth_of[other] = depth
                parent[other] = (e['fromCollection'], e['fromId'], e['field'])
                fresh.append(other)
                edges.append({**e, 'depth': depth})
            frontier = fresh

    # 水合节点数据并重建路径
    data_map = load_node_pairs(list(seen))
    nodes = []
    for key in sorted(seen, key=lambda k: (depth_of.get(k, 0), k[0], k[1])):
        path = [key]
        cur_key = key
        while cur_key in parent:
            pcol, pid, _pfield = parent[cur_key]
            cur_key = pcol, pid
            path.append(cur_key)
        path.reverse()
        nodes.append({'collection': key[0], 'id': key[1],
                      'depth': depth_of.get(key, 0),
                      'viaField': parent.get(key, (None, None, None))[2]
                      if key in parent else None,
                      'path': [f'{c}/{i}' for c, i in path],
                      'data': data_map.get(key)})

    nodes.sort(key=lambda n: (n['depth'], n['collection'], n['id']))
    return {
        'start': {'collection': collection, 'id': record_id, 'data': center_data},
        'direction': direction,
        'maxDepth': max_depth,
        'nodeCount': len(nodes),
        'edgeCount': len(edges),
        'truncated': truncated,
        'nodes': nodes,
        'edges': edges,
    }
