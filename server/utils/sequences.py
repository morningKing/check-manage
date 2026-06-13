"""autoSequence 后端原子分配 + 计数器播种（迁移与还原共用）。"""


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
        if prefix and not val.startswith(prefix):
            continue
        s = val[plen:] if prefix else val
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
    branch_id=None 时对数据中出现的所有分支播种。
    调用方负责提交事务（本函数不 commit）。"""
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


def allocate_sequence(cur, collection, branch_id, field_name, prefix, pad, count=1):
    """原子分配 count 个序列值，返回格式化字符串列表。
    先以现有数据 max 建零行（ON CONFLICT DO NOTHING，幂等），再 SELECT ... FOR UPDATE
    锁定计数行串行化分配，递增 count。必须在调用方事务内执行（提交前不释放行锁）。
    调用方负责提交。"""
    base_seed = seq_max_from_data(cur, collection, branch_id, field_name, prefix)
    cur.execute(
        "INSERT INTO dynamic_sequences (collection, branch_id, field_name, current_value) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (collection, branch_id, field_name) DO NOTHING",
        (collection, branch_id, field_name, base_seed),
    )
    cur.execute(
        "SELECT current_value FROM dynamic_sequences "
        "WHERE collection=%s AND branch_id=%s AND field_name=%s FOR UPDATE",
        (collection, branch_id, field_name),
    )
    base = cur.fetchone()[0]
    new_value = base + count
    cur.execute(
        "UPDATE dynamic_sequences SET current_value=%s "
        "WHERE collection=%s AND branch_id=%s AND field_name=%s",
        (new_value, collection, branch_id, field_name),
    )
    start = base + 1
    return [f"{prefix}{n:0{pad}d}" for n in range(start, start + count)]
