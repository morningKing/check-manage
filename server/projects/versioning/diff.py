from db import get_db

from .repo import MAIN_BRANCH_ID, get_project_collections, get_user_project_branch


# ==================== 新增功能：版本对比、合并、恢复 ====================

def load_project_version_data(collection, branch_id):
    """
    加载指定分支的数据（用于对比）

    Parameters
    ----------
    collection : str
        Collection 名称
    branch_id : str
        分支 ID，'main' 表示主分支

    Returns
    -------
    tuple
        (records, relations_map)
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, data FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection, branch_id),
        )
        data_rows = cur.fetchall()

        cur.execute(
            'SELECT record_id, field_name, related_id FROM data_relations '
            'WHERE collection = %s AND branch_id = %s',
            (collection, branch_id),
        )
        rel_rows = cur.fetchall()

    # 构建记录列表
    records = []
    for rid, data in data_rows:
        flat = {'id': rid}
        if isinstance(data, dict):
            flat.update(data)
        records.append(flat)

    # 构建关联映射
    rel_map = {}
    for record_id, field_name, related_id in rel_rows:
        rel_map.setdefault(record_id, {}).setdefault(field_name, []).append(related_id)

    return records, rel_map


def load_project_snapshot_data(version_id, collection):
    """
    从快照加载版本数据

    Parameters
    ----------
    version_id : str
        版本 ID
    collection : str
        Collection 名称

    Returns
    -------
    tuple
        (records, relations_map)
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 加载快照数据
        cur.execute(
            'SELECT record_id, record_data FROM project_version_snapshots '
            'WHERE version_id = %s AND collection = %s',
            (version_id, collection),
        )
        snapshot_rows = cur.fetchall()

        # 加载关联数据
        cur.execute(
            'SELECT record_id, field_name, related_id FROM project_version_relations '
            'WHERE version_id = %s AND collection = %s',
            (version_id, collection),
        )
        rel_rows = cur.fetchall()

    # 构建记录列表
    records = []
    for rid, data in snapshot_rows:
        flat = {'id': rid}
        if isinstance(data, dict):
            flat.update(data)
        records.append(flat)

    # 构建关联映射
    rel_map = {}
    for record_id, field_name, related_id in rel_rows:
        rel_map.setdefault(record_id, {}).setdefault(field_name, []).append(related_id)

    return records, rel_map


def get_collection_fields(collection):
    """
    获取 Collection 的字段配置

    Parameters
    ----------
    collection : str
        Collection 名称

    Returns
    -------
    list[dict]
        字段配置列表
    """
    with get_db() as conn:
        cur = conn.cursor()
        page_id = f'page-{collection}'
        cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else []


def compute_collection_diff(base_records, target_records, field_names, base_rels=None, target_rels=None, relation_fields=None):
    """
    计算两个 Collection 数据的差异

    Parameters
    ----------
    base_records : list[dict]
        基准数据
    target_records : list[dict]
        对比数据
    field_names : list[str]
        要比较的字段名列表
    base_rels : dict | None
        基准关联数据
    target_rels : dict | None
        对比关联数据
    relation_fields : list[dict] | None
        关联字段配置

    Returns
    -------
    dict
        {added, removed, modified, unchangedCount}
    """
    # 合并关联数据到记录中
    if relation_fields and base_rels is not None and target_rels is not None:
        for rec in base_records:
            rid = rec['id']
            rec_rels = base_rels.get(rid, {})
            for rf in relation_fields:
                fn = rf['fieldName']
                rec[fn] = sorted(rec_rels.get(fn, []))
        for rec in target_records:
            rid = rec['id']
            rec_rels = target_rels.get(rid, {})
            for rf in relation_fields:
                fn = rf['fieldName']
                rec[fn] = sorted(rec_rels.get(fn, []))

    base_map = {r['id']: r for r in base_records}
    target_map = {r['id']: r for r in target_records}

    base_ids = set(base_map.keys())
    target_ids = set(target_map.keys())

    added = []
    for rid in sorted(target_ids - base_ids):
        added.append(target_map[rid])

    removed = []
    for rid in sorted(base_ids - target_ids):
        removed.append(base_map[rid])

    modified = []
    unchanged_count = 0
    for rid in sorted(base_ids & target_ids):
        old = base_map[rid]
        new = target_map[rid]
        changed_fields = []
        for fn in field_names:
            old_val = old.get(fn)
            new_val = new.get(fn)
            if old_val != new_val:
                changed_fields.append({
                    'fieldName': fn,
                    'oldValue': old_val,
                    'newValue': new_val,
                })
        if changed_fields:
            modified.append({
                'id': rid,
                'record': new,
                'oldRecord': old,
                'fields': changed_fields,
            })
        else:
            unchanged_count += 1

    return {
        'added': added,
        'removed': removed,
        'modified': modified,
        'unchangedCount': unchanged_count,
    }


def load_project_version_data_by_type(version_id, collection, version_type=None):
    """
    根据版本类型加载版本数据

    Parameters
    ----------
    version_id : str
        版本 ID
    collection : str
        Collection 名称
    version_type : str | None
        版本类型，'snapshot' 或 'branch'。如果为 None，则从数据库查询

    Returns
    -------
    tuple
        (records, relations_map)
    """
    if version_type is None:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT version_type FROM project_versions WHERE id = %s', (version_id,))
            row = cur.fetchone()
            if row:
                version_type = row[0]
            else:
                version_type = 'snapshot'  # 默认按快照处理

    if version_type == 'branch':
        # 分支类型：从 dynamic_data 加载当前分支数据
        return load_project_version_data(collection, version_id)
    else:
        # 快照类型：从快照表加载
        return load_project_snapshot_data(version_id, collection)


def compute_project_version_diff(project_menu_id, base_version, target_version, user_id):
    """
    计算项目版本差异（跨所有 Collection）

    Parameters
    ----------
    project_menu_id : str
        项目菜单 ID
    base_version : str
        基准版本，'main' 表示主分支，'current' 表示用户当前分支，其他为版本 ID
    target_version : str
        对比版本，同上
    user_id : str
        用户 ID

    Returns
    -------
    dict
        {
            'collections': [
                {
                    'collection': 'xxx',
                    'pageName': 'xxx',
                    'added': [...],
                    'removed': [...],
                    'modified': [...],
                    'unchangedCount': N
                }
            ],
            'totalAdded': N,
            'totalRemoved': M,
            'totalModified': K,
            'totalUnchanged': L
        }
    """
    # 获取项目下所有 Collection
    collections = get_project_collections(project_menu_id)

    # 获取用户当前分支
    current_branch = get_user_project_branch(user_id, project_menu_id)

    diff_result = {
        'collections': [],
        'totalAdded': 0,
        'totalRemoved': 0,
        'totalModified': 0,
        'totalUnchanged': 0,
    }

    for coll_info in collections:
        collection = coll_info['collection']

        # 解析基准版本
        if base_version == 'main':
            base_branch_id = MAIN_BRANCH_ID
            base_records, base_rels = load_project_version_data(collection, base_branch_id)
        elif base_version == 'current':
            base_branch_id = current_branch
            base_records, base_rels = load_project_version_data(collection, base_branch_id)
        else:
            # 根据版本类型加载（分支从 dynamic_data，快照从 snapshots）
            base_records, base_rels = load_project_version_data_by_type(base_version, collection)

        # 解析目标版本
        if target_version == 'main':
            target_branch_id = MAIN_BRANCH_ID
            target_records, target_rels = load_project_version_data(collection, target_branch_id)
        elif target_version == 'current':
            target_branch_id = current_branch
            target_records, target_rels = load_project_version_data(collection, target_branch_id)
        else:
            # 根据版本类型加载（分支从 dynamic_data，快照从 snapshots）
            target_records, target_rels = load_project_version_data_by_type(target_version, collection)

        # 获取字段配置
        fields = get_collection_fields(collection)
        field_names = [f['fieldName'] for f in fields]
        relation_fields = [f for f in fields if f.get('controlType') == 'relation']

        # 计算差异
        coll_diff = compute_collection_diff(
            base_records, target_records, field_names,
            base_rels, target_rels, relation_fields
        )

        diff_result['collections'].append({
            'collection': collection,
            'pageName': coll_info['pageName'],
            'added': coll_diff['added'],
            'removed': coll_diff['removed'],
            'modified': coll_diff['modified'],
            'unchangedCount': coll_diff['unchangedCount'],
        })

        diff_result['totalAdded'] += len(coll_diff['added'])
        diff_result['totalRemoved'] += len(coll_diff['removed'])
        diff_result['totalModified'] += len(coll_diff['modified'])
        diff_result['totalUnchanged'] += coll_diff['unchangedCount']

    return diff_result



__all__ = [
    'get_db',
    'load_project_version_data',
    'load_project_snapshot_data',
    'get_collection_fields',
    'compute_collection_diff',
    'load_project_version_data_by_type',
    'compute_project_version_diff',
]
