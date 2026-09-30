"""
页面配置关联关系扫描工具

使用BFS递归扫描页面配置之间的关联关系
"""

from db import get_db
import psycopg2.extras


def extract_target_collection(field: dict) -> str | None:
    """根据字段类型提取目标集合ID"""

    control_type = field.get('controlType')

    if control_type == 'relation':
        config = field.get('relationConfig', {})
        return config.get('targetCollection')

    elif control_type == 'reference':
        config = field.get('referenceConfig', {})
        return config.get('targetCollection')

    elif control_type == 'quoteSelect':
        config = field.get('quoteConfig', {})
        return config.get('targetCollection')

    return None


def get_page_config_relations(page_id: str, max_depth: int = 3):
    """
    获取页面配置的关联关系

    Args:
        page_id: 页面配置ID
        max_depth: 最大递归深度(默认3层)

    Returns:
        dict: {nodes: [...], edges: [...]}
    """
    print(f"[DEBUG] get_page_config_relations called with page_id={page_id}, max_depth={max_depth}")

    visited = set()
    nodes = []
    edges = []

    # BFS队列
    queue = [(page_id, 0)]

    while queue:
        current_id, depth = queue.pop(0)

        print(f"[DEBUG] Processing: current_id={current_id}, depth={depth}")

        if current_id in visited or depth > max_depth:
            print(f"[DEBUG] Skipping (visited={current_id in visited}, depth>{max_depth})")
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
                print(f"[DEBUG] Page config not found for {current_id}")
                continue

            print(f"[DEBUG] Found page config: {page_config[1]}")

            nodes.append({
                'id': current_id,
                'name': page_config[1],
                'fields': len(page_config[2]) if page_config[2] else 0
            })

            # 扫描字段关联
            fields = page_config[2] or []
            for field in fields:
                target_collection = extract_target_collection(field)

                if target_collection:
                    print(f"[DEBUG] Found relation: field={field['fieldName']}, target_collection={target_collection}")

                    # 将 collection 名称转换为 page_id
                    target_page_id = f'page-{target_collection}'
                    print(f"[DEBUG] Converted to target_page_id={target_page_id}")

                    edges.append({
                        'source': current_id,
                        'target': target_page_id,
                        'type': field['controlType'],
                        'field': field['fieldName'],
                        'label': field.get('label', field['fieldName'])
                    })

                    if target_page_id not in visited:
                        queue.append((target_page_id, depth + 1))
                        print(f"[DEBUG] Added to queue: {target_page_id}")

    print(f"[DEBUG] Final result: {len(nodes)} nodes, {len(edges)} edges")
    return {'nodes': nodes, 'edges': edges}