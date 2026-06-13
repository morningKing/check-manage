"""autoSequence 后端原子分配 + 计数器播种（迁移与还原共用）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _autoseq_fields_by_collection(cur, collections=None):
    """返回 {collection: [(field_name, prefix), ...]}，仅 autoSequence 字段。"""
    if collections:
        page_ids = [f'page-{c}' for c in collections]
        cur.execute("SELECT id, fields FROM page_configs WHERE id = ANY(%s)", (page_ids,))
    else:
        cur.execute("SELECT id, fields FROM page_configs")
    out = {}
    for pid, fields in cur.fetchall():
        coll = pid[len('page-'):] if pid.startswith('page-') else pid
        for f in (fields or []):
            if f.get('controlType') == 'autoSequence':
                prefix = (f.get('sequenceConfig') or {}).get('prefix', '')
                out.setdefault(coll, []).append((f['fieldName'], prefix))
    return out


def seq_max_from_data(cur, collection, branch_id, field_name, prefix):
    """扫描 dynamic_data，取该字段去前缀后的最大数值；无则 0。"""
    cur.execute(
        "SELECT data->>%s FROM dynamic_data WHERE collection=%s AND branch_id=%s AND data ? %s",
        (field_name, collection, branch_id, field_name),
    )
    mx = 0
    plen = len(prefix)
    for (val,) in cur.fetchall():
        if not isinstance(val, str):
            continue
        s = val[plen:] if (prefix and val.startswith(prefix)) else val
        try:
            n = int(s)
        except (ValueError, TypeError):
            continue
        if n > mx:
            mx = n
    return mx


def reseed_sequences(cur, collections=None, branch_id=None):
    """为每个 (collection, branch, autoSequence字段) 重播种计数器。
    GREATEST 语义：current_value = max(已有计数, 数据中的 max)，绝不回退。
    branch_id=None 时对数据中出现的所有分支播种。"""
    fields_map = _autoseq_fields_by_collection(cur, collections)
    for coll, fld_list in fields_map.items():
        if branch_id is not None:
            branches = [branch_id]
        else:
            cur.execute("SELECT DISTINCT branch_id FROM dynamic_data WHERE collection=%s", (coll,))
            branches = [r[0] for r in cur.fetchall()] or ['main']
        for br in branches:
            for field_name, prefix in fld_list:
                mx = seq_max_from_data(cur, coll, br, field_name, prefix)
                cur.execute(
                    "INSERT INTO dynamic_sequences (collection, branch_id, field_name, current_value) "
                    "VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (collection, branch_id, field_name) "
                    "DO UPDATE SET current_value = GREATEST(dynamic_sequences.current_value, EXCLUDED.current_value)",
                    (coll, br, field_name, mx),
                )
