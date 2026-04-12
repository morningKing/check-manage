"""
页面配置关联关系扫描工具

使用BFS递归扫描页面配置之间的关联关系
"""

from db import get_db
import psycopg2.extras


def get_page_config_relations(page_id: str, max_depth: int = 3):
    """
    获取页面配置的关联关系

    Args:
        page_id: 页面配置ID
        max_depth: 最大递归深度(默认3层)

    Returns:
        dict: {nodes: [...], edges: [...]}
    """
    visited = set()
    nodes = []
    edges = []

    # BFS队列
    queue = [(page_id, 0)]

    while queue:
        current_id, depth = queue.pop(0)

        if current_id in visited or depth > max_depth:
            continue

        visited.add(current_id)

        # 获取页面配置
        with get_db() as conn:
            cur = conn.cursor()

            cur.execute(
                'SELECT id, name, fields FROM page_configs WHERE id = %s',
                (current_id,)
            )
            page_config = cur.fetchone()

            if not page_config:
                continue

            nodes.append({
                'id': current_id,
                'name': page_config[1],
                'fields': len(page_config[2]) if page_config[2] else 0
            })

    return {'nodes': nodes, 'edges': edges}