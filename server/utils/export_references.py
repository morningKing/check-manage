"""菜单级导出的引用解析（方案 A）。

菜单级导出脚本只拿到选中页面的 `dynamic_data.data`，既看不到 `data_relations`（M:N
relation 的外键），也没有被引用页面的记录本身——于是 reference / quoteSelect / relation
的外键 ID 在导出物里都是「裸 ID」，跨项目时被引用页根本不在选中范围，脚本无从 join。

本模块在跑脚本前自动解析引用：
  1. 扫描选中记录的引用来源
     - 内联：reference（`referenceConfig.targetCollection`，单值）/ quoteSelect
       （`quoteConfig.targetCollection`，多值）——ID 就在 `record[fieldName]`；
     - 关系表：`data_relations`——把每条记录的关系链回挂到 `record['_relations']`。
  2. 收集被引用的 `(target_collection, id)`，**按跨项目依赖声明的版本/分支**补取这些记录
     （read-only → pinned_version；否则 → target_branch；无依赖 → 导出分支回退 main）。
  3. 返回 `references = {collection: {id: record}}` 供脚本 join，无需把整张被引用页都导出。

`menu_data` 会被就地补上每条记录的 `_relations`。
"""


def _reference_target_map(fields):
    """从字段配置提取 {fieldName: (target_collection, is_multi)}，仅 reference / quoteSelect。"""
    out = {}
    for f in (fields or []):
        ctype = f.get('controlType')
        name = f.get('fieldName')
        if not name:
            continue
        if ctype == 'reference':
            tc = (f.get('referenceConfig') or {}).get('targetCollection')
            if tc:
                out[name] = (tc, False)
        elif ctype == 'quoteSelect':
            tc = (f.get('quoteConfig') or {}).get('targetCollection')
            if tc:
                out[name] = (tc, True)
    return out


def _build_dependency_branch_map(cur):
    """构建 (source_collection, target_collection) -> 解析分支 的映射。

    read-only 且有 pinned_version → 用 pinned_version 作为被引用记录的 branch_id；
    其余（read-write / track-main）→ 用依赖的 target_branch。无声明则不入表（由调用方回退）。
    """
    cur.execute(
        "SELECT r.source_collection, r.target_collection, d.relation_type, "
        "       d.pinned_version, d.target_branch "
        "FROM project_dependency_relations r "
        "JOIN project_dependencies d ON r.dependency_id = d.id"
    )
    mapping = {}
    for src, tgt, rel_type, pinned, target_branch in cur.fetchall():
        branch = pinned if (rel_type == 'read-only' and pinned) else (target_branch or 'main')
        mapping.setdefault((src, tgt), branch)  # 同一 (src,tgt) 以首条为准
    return mapping


def _fetch_record(cur, collection, record_id, branch_candidates):
    """按候选分支顺序取记录，命中即返回 {id, ...data}；都没有则 None。"""
    tried = set()
    for b in branch_candidates:
        if not b or b in tried:
            continue
        tried.add(b)
        cur.execute(
            "SELECT id, data FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s",
            (collection, record_id, b),
        )
        row = cur.fetchone()
        if row:
            rec = {'id': row[0]}
            if row[1]:
                rec.update(row[1])
            return rec
    return None


def _inline_refs(src_collection, record, ref_map):
    """产出某记录内联 reference/quoteSelect 的待解析三元组 (src_collection, target_collection, id)。"""
    out = []
    for fname, (tc, is_multi) in ref_map.items():
        val = record.get(fname)
        if val in (None, '', []):
            continue
        ids = val if (is_multi and isinstance(val, list)) else [val]
        for rid in ids:
            if rid:
                out.append((src_collection, tc, str(rid)))
    return out


def _target_field_map(cur, collection):
    """取某集合的 reference/quoteSelect 字段映射（闭包展开下一层时用）。"""
    cur.execute("SELECT fields FROM page_configs WHERE id = %s", (f'page-{collection}',))
    row = cur.fetchone()
    return _reference_target_map(row[0] if row else [])


def resolve_references(cur, menu_data, export_branch='main', max_depth=1):
    """解析 menu_data 中记录的引用，返回 references 查找表；就地给记录补 `_relations`。

    references: {collection: {id: {id, ...data} | None}}，只含被引用到的记录；
                值为 None 表示该 ID 已尝试但缺失（脚本可据此识别悬挂引用）。
    max_depth: 传递闭包深度（被引用记录又引用别人）。默认 1（直接引用），防无界展开。
    """
    dep_branch = _build_dependency_branch_map(cur)
    references = {}

    def candidates(src_collection, target_collection):
        return [dep_branch.get((src_collection, target_collection)), export_branch, 'main']

    # ---- Level 0：从 menu_data 收集内联引用 + data_relations ----
    to_resolve = []  # [(src_collection, target_collection, id)]
    for table in menu_data:
        coll = table.get('collection')
        records = table.get('records') or []
        ref_map = _reference_target_map(table.get('fields'))
        rec_by_id = {}
        for rec in records:
            rec_by_id[rec.get('id')] = rec
            to_resolve.extend(_inline_refs(coll, rec, ref_map))

        ids = [rid for rid in rec_by_id if rid]
        if ids:
            cur.execute(
                "SELECT record_id, field_name, related_collection, related_id "
                "FROM data_relations WHERE collection = %s AND branch_id = %s "
                "AND record_id = ANY(%s)",
                (coll, export_branch, ids),
            )
            for record_id, field_name, related_collection, related_id in cur.fetchall():
                rec = rec_by_id.get(record_id)
                if rec is not None:
                    rec.setdefault('_relations', {}).setdefault(field_name, []).append(related_id)
                if related_collection and related_id:
                    to_resolve.append((coll, related_collection, related_id))

    # ---- 逐层解析（BFS），闭包只跟随内联引用，不再展开关系表，避免过深 ----
    field_map_cache = {}
    for level in range(max_depth):
        next_level = []
        for src_collection, tc, rid in to_resolve:
            bucket = references.setdefault(tc, {})
            if rid in bucket:
                continue
            rec = _fetch_record(cur, tc, rid, candidates(src_collection, tc))
            bucket[rid] = rec
            if rec is not None and level + 1 < max_depth:
                if tc not in field_map_cache:
                    field_map_cache[tc] = _target_field_map(cur, tc)
                next_level.extend(_inline_refs(tc, rec, field_map_cache[tc]))
        to_resolve = next_level
        if not to_resolve:
            break

    return references
