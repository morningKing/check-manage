"""
项目版本管理核心逻辑

职责：
- 项目级快照/分支创建
- 项目分支切换（同步切换所有collection）
- 项目分支合并
- 项目版本差异对比

设计：
- 项目分支覆盖数据分支
- 项目下所有数据菜单共享同一个分支状态
- 参考 Git 的分支管理模式
"""

import uuid
import hashlib
from datetime import datetime, timezone
from db import get_db
import psycopg2.extras

MAIN_BRANCH_ID = 'main'


def get_project_collections(project_menu_id):
    """
    获取项目下所有数据菜单对应的collection列表

    Parameters
    ----------
    project_menu_id : str
        项目菜单ID

    Returns
    -------
    list[dict]
        collection列表，每项包含 collection, pageId, pageName
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 查询项目菜单的所有子菜单（数据菜单）
        cur.execute(
            'SELECT id, page_id FROM menus WHERE parent_id = %s AND menu_type = %s',
            (project_menu_id, 'data')
        )
        data_menus = cur.fetchall()

        collections = []
        for menu_id, page_id in data_menus:
            if not page_id:
                continue

            # 从 page_config 获取信息
            cur.execute(
                'SELECT name FROM page_configs WHERE id = %s',
                (page_id,)
            )
            page_row = cur.fetchone()
            if page_row:
                # collection 名称：page-xxx -> xxx
                collection_name = page_id.replace('page-', '')
                collections.append({
                    'collection': collection_name,
                    'pageId': page_id,
                    'pageName': page_row[0],
                    'menuId': menu_id
                })

        return collections


def get_user_project_branch(user_id, project_menu_id):
    """
    获取用户在项目的当前分支

    Parameters
    ----------
    user_id : str
        用户ID
    project_menu_id : str
        项目菜单ID

    Returns
    -------
    str
        分支ID，默认为 'main'
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT branch_id FROM user_current_project_branch WHERE user_id = %s AND project_menu_id = %s',
            (user_id, project_menu_id)
        )
        row = cur.fetchone()
        return row[0] if row else MAIN_BRANCH_ID


def set_user_project_branch(user_id, username, project_menu_id, branch_id):
    """
    设置用户在项目的当前分支

    Parameters
    ----------
    user_id : str
        用户ID
    username : str
        用户名
    project_menu_id : str
        项目菜单ID
    branch_id : str
        分支ID
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 检查是否存在
        cur.execute(
            'SELECT id FROM user_current_project_branch WHERE user_id = %s AND project_menu_id = %s',
            (user_id, project_menu_id)
        )
        row = cur.fetchone()

        now = datetime.now(timezone.utc)
        if row:
            cur.execute(
                'UPDATE user_current_project_branch SET branch_id = %s, updated_at = %s WHERE user_id = %s AND project_menu_id = %s',
                (branch_id, now, user_id, project_menu_id)
            )
        else:
            new_id = f'ucpb-{uuid.uuid4().hex[:8]}'
            cur.execute(
                'INSERT INTO user_current_project_branch (id, user_id, username, project_menu_id, branch_id, updated_at) VALUES (%s, %s, %s, %s, %s, %s)',
                (new_id, user_id, username, project_menu_id, branch_id, now)
            )


def create_project_version(project_menu_id, name, description, version_type, created_by, parent_version=None):
    """
    创建项目版本（快照或分支）

    Parameters
    ----------
    project_menu_id : str
        项目菜单ID
    name : str
        版本名称
    description : str
        版本描述
    version_type : str
        'snapshot' 或 'branch'
    created_by : str
        创建者用户名
    parent_version : str | None
        父版本ID（用于分支）

    Returns
    -------
    dict
        创建的版本信息
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 获取项目的所有collection
        collections = get_project_collections(project_menu_id)

        if not collections:
            raise ValueError('项目下没有数据菜单，无法创建版本')

        # 获取创建者ID
        cur.execute('SELECT id FROM users WHERE username = %s', (created_by,))
        user_row = cur.fetchone()
        user_id = user_row[0] if user_row else None

        # 获取当前分支
        current_branch = get_user_project_branch(user_id, project_menu_id) if user_id else MAIN_BRANCH_ID

        # 创建版本记录
        version_id = f'prj-ver-{uuid.uuid4().hex[:8]}'
        now = datetime.now(timezone.utc)

        cur.execute(
            'INSERT INTO project_versions (id, project_menu_id, name, description, version_type, created_by, created_at, parent_version) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
            (version_id, project_menu_id, name, description, version_type, created_by, now, parent_version)
        )

        # 复制每个collection的数据到快照
        total_records = 0
        total_relations = 0

        for coll_info in collections:
            collection = coll_info['collection']

            # 查询数据
            cur.execute(
                'SELECT id, data, created_at FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (collection, current_branch)
            )
            records = cur.fetchall()

            for record_id, data, created_at in records:
                cur.execute(
                    'INSERT INTO project_version_snapshots (version_id, collection, record_id, record_data, created_at) '
                    'VALUES (%s, %s, %s, %s, %s)',
                    (version_id, collection, record_id, psycopg2.extras.Json(data), created_at)
                )
                total_records += 1

            # 查询关联数据
            cur.execute(
                'SELECT collection, record_id, field_name, related_collection, related_id FROM data_relations '
                'WHERE collection = %s AND branch_id = %s',
                (collection, current_branch)
            )
            relations = cur.fetchall()

            for rel_coll, record_id, field_name, related_coll, related_id in relations:
                cur.execute(
                    'INSERT INTO project_version_relations (version_id, collection, record_id, field_name, related_collection, related_id) '
                    'VALUES (%s, %s, %s, %s, %s, %s)',
                    (version_id, rel_coll, record_id, field_name, related_coll, related_id)
                )
                total_relations += 1

        # 更新版本记录数
        cur.execute(
            'UPDATE project_versions SET records_count = %s WHERE id = %s',
            (total_records, version_id)
        )

        return {
            'id': version_id,
            'projectMenuId': project_menu_id,
            'name': name,
            'description': description,
            'versionType': version_type,
            'createdBy': created_by,
            'createdAt': now.isoformat(),
            'parentVersion': parent_version,
            'collectionsCount': len(collections),
            'recordsCount': total_records,
            'relationsCount': total_relations,
        }


def list_project_versions(project_menu_id, page=1, pageSize=20):
    """
    获取项目的版本列表

    Parameters
    ----------
    project_menu_id : str
        项目菜单ID
    page : int
        页码
    pageSize : int
        每页数量

    Returns
    -------
    dict
        包含 items, total, page, pageSize
    """
    with get_db() as conn:
        cur = conn.cursor()

        offset = (page - 1) * pageSize

        cur.execute(
            'SELECT COUNT(*) FROM project_versions WHERE project_menu_id = %s',
            (project_menu_id,)
        )
        total = cur.fetchone()[0]

        cur.execute(
            'SELECT id, name, description, version_type, status, created_by, created_at, '
            'parent_version, records_count, is_protected '
            'FROM project_versions WHERE project_menu_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s',
            (project_menu_id, pageSize, offset)
        )
        rows = cur.fetchall()

        items = []
        for row in rows:
            items.append({
                'id': row[0],
                'name': row[1],
                'description': row[2],
                'versionType': row[3],
                'status': row[4],
                'createdBy': row[5],
                'createdAt': row[6].isoformat() if row[6] else None,
                'parentVersion': row[7],
                'recordsCount': row[8],
                'isProtected': row[9],
            })

        return {
            'items': items,
            'total': total,
            'page': page,
            'pageSize': pageSize,
        }


def switch_project_branch(user_id, username, project_menu_id, version_id):
    """
    切换项目分支

    同步切换所有collection的分支状态

    Parameters
    ----------
    user_id : str
        用户ID
    username : str
        用户名
    project_menu_id : str
        项目菜单ID
    version_id : str
        目标版本ID

    Returns
    -------
    dict
        切换结果，包含影响的信息
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 检查版本是否存在且为分支类型
        cur.execute(
            'SELECT id, name, version_type, status FROM project_versions WHERE id = %s AND project_menu_id = %s',
            (version_id, project_menu_id)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')
        if row[2] != 'branch':
            raise ValueError('只能切换到分支类型，快照不可切换')
        if row[3] != 'active':
            raise ValueError('版本状态不是active，无法切换')

        branch_name = row[1]

        # 获取项目的所有collection
        collections = get_project_collections(project_menu_id)

        # 使用版本ID作为分支ID
        branch_id = version_id

        # 更新用户的项目分支状态
        set_user_project_branch(user_id, username, project_menu_id, branch_id)

        # 同步更新所有collection的分支状态
        from utils.version import set_user_current_branch

        for coll_info in collections:
            set_user_current_branch(user_id, username, coll_info['collection'], branch_id)

        # 如果是新分支首次切换，需要初始化数据（从快照复制）
        cur.execute(
            'SELECT initialized_at FROM project_versions WHERE id = %s',
            (version_id,)
        )
        init_row = cur.fetchone()
        if not init_row or not init_row[0]:
            # 初始化分支数据
            initialize_project_branch_data(version_id, collections, cur)
            cur.execute(
                'UPDATE project_versions SET initialized_at = %s WHERE id = %s',
                (datetime.now(timezone.utc), version_id)
            )

        return {
            'branchId': branch_id,
            'branchName': branch_name,
            'affectedCollections': [c['collection'] for c in collections],
            'collectionsCount': len(collections),
        }


def initialize_project_branch_data(version_id, collections, cur):
    """
    从快照初始化分支数据

    Parameters
    ----------
    version_id : str
        版本ID
    collections : list
        collection列表
    cur : cursor
        数据库游标
    """
    for coll_info in collections:
        collection = coll_info['collection']

        # 查询快照数据
        cur.execute(
            'SELECT record_id, record_data, created_at FROM project_version_snapshots '
            'WHERE version_id = %s AND collection = %s',
            (version_id, collection)
        )
        records = cur.fetchall()

        # 插入到 dynamic_data
        for record_id, data, created_at in records:
            cur.execute(
                'INSERT INTO dynamic_data (id, collection, data, created_at, branch_id) '
                'VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id, branch_id) DO UPDATE SET data = EXCLUDED.data',
                (record_id, collection, psycopg2.extras.Json(data), created_at, version_id)
            )

        # 查询快照关联数据
        cur.execute(
            'SELECT collection, record_id, field_name, related_collection, related_id FROM project_version_relations '
            'WHERE version_id = %s AND collection = %s',
            (version_id, collection)
        )
        relations = cur.fetchall()

        # 插入到 data_relations
        for rel_coll, record_id, field_name, related_coll, related_id in relations:
            cur.execute(
                'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
                'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                (rel_coll, record_id, field_name, related_coll, related_id, version_id)
            )


def get_project_version_detail(version_id):
    """
    获取项目版本详情

    Parameters
    ----------
    version_id : str
        版本ID

    Returns
    -------
    dict
        版本详情
    """
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            'SELECT id, project_menu_id, name, description, version_type, status, created_by, created_at, '
            'parent_version, records_count, is_protected '
            'FROM project_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            return None

        project_menu_id = row[1]
        collections = get_project_collections(project_menu_id)

        return {
            'id': row[0],
            'projectMenuId': row[1],
            'name': row[2],
            'description': row[3],
            'versionType': row[4],
            'status': row[5],
            'createdBy': row[6],
            'createdAt': row[7].isoformat() if row[7] else None,
            'parentVersion': row[8],
            'recordsCount': row[9],
            'isProtected': row[10],
            'collections': collections,
        }


def delete_project_version(version_id):
    """
    删除项目版本

    Parameters
    ----------
    version_id : str
        版本ID

    Returns
    -------
    bool
        是否成功
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 检查是否受保护
        cur.execute('SELECT is_protected FROM project_versions WHERE id = %s', (version_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')
        if row[0]:
            raise ValueError('受保护的版本不能删除')

        # 检查是否有子分支
        cur.execute('SELECT COUNT(*) FROM project_versions WHERE parent_version = %s', (version_id,))
        child_count = cur.fetchone()[0]
        if child_count > 0:
            raise ValueError('存在子分支，无法删除')

        # 删除（级联删除快照数据）
        cur.execute('DELETE FROM project_versions WHERE id = %s', (version_id,))

        return True


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
            # 从快照加载
            base_records, base_rels = load_project_snapshot_data(base_version, collection)

        # 解析目标版本
        if target_version == 'main':
            target_branch_id = MAIN_BRANCH_ID
            target_records, target_rels = load_project_version_data(collection, target_branch_id)
        elif target_version == 'current':
            target_branch_id = current_branch
            target_records, target_rels = load_project_version_data(collection, target_branch_id)
        else:
            # 从快照加载
            target_records, target_rels = load_project_snapshot_data(target_version, collection)

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


def merge_project_version(version_id, target_branch, strategy, merged_by, user_id, project_menu_id):
    """
    合并项目版本到目标分支

    Parameters
    ----------
    version_id : str
        源版本 ID
    target_branch : str
        目标分支，'main' 或 'current'
    strategy : str
        合并策略：'theirs'（使用源版本数据）或 'ours'（保留当前）
    merged_by : str
        合并执行者
    user_id : str
        用户 ID
    project_menu_id : str
        项目菜单 ID

    Returns
    -------
    dict
        {
            'success': True,
            'collections': [
                {'collection': 'xxx', 'recordsCreated': N, 'recordsUpdated': M, 'recordsDeleted': K}
            ]
        }
    """
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        cur = conn.cursor()

        # 获取版本信息
        cur.execute(
            'SELECT name, version_type, status FROM project_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')
        version_name, version_type, status = row

        if status == 'merged':
            raise ValueError('该版本已被合并')

        # 获取目标分支
        if target_branch == 'current':
            target_branch_id = get_user_project_branch(user_id, project_menu_id)
        else:
            target_branch_id = MAIN_BRANCH_ID

        # 获取项目下所有 Collection
        collections = get_project_collections(project_menu_id)

        merge_result = {'success': True, 'collections': []}

        for coll_info in collections:
            collection = coll_info['collection']

            # 加载源版本数据
            source_records, source_rels = load_project_snapshot_data(version_id, collection)

            # 加载目标分支当前数据
            target_records, target_rels = load_project_version_data(collection, target_branch_id)

            # 获取字段配置
            fields = get_collection_fields(collection)
            field_names = [f['fieldName'] for f in fields]
            relation_fields = [f for f in fields if f.get('controlType') == 'relation']

            # 计算差异
            diff = compute_collection_diff(
                target_records, source_records, field_names,
                target_rels, source_rels, relation_fields
            )

            coll_result = {
                'collection': collection,
                'pageName': coll_info['pageName'],
                'recordsCreated': 0,
                'recordsUpdated': 0,
                'recordsDeleted': 0,
            }

            if strategy == 'theirs':
                # 使用源版本数据

                # 1. 删除目标数据中不在源版本中的记录
                for rec in diff['removed']:
                    cur.execute(
                        'DELETE FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                        (collection, rec['id'], target_branch_id),
                    )
                    cur.execute(
                        'DELETE FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                        (collection, rec['id'], target_branch_id),
                    )
                    coll_result['recordsDeleted'] += 1

                # 2. 插入新增记录
                for rec in diff['added']:
                    data = {k: v for k, v in rec.items() if k != 'id'}
                    cur.execute(
                        'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
                        (rec['id'], collection, psycopg2.extras.Json(data), target_branch_id),
                    )
                    coll_result['recordsCreated'] += 1

                # 3. 更新修改记录
                for item in diff['modified']:
                    new_data = {k: v for k, v in item['record'].items() if k != 'id'}
                    cur.execute(
                        'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 '
                        'WHERE collection = %s AND id = %s AND branch_id = %s',
                        (psycopg2.extras.Json(new_data), collection, item['id'], target_branch_id),
                    )
                    coll_result['recordsUpdated'] += 1

                # 4. 恢复关联数据
                cur.execute(
                    'DELETE FROM data_relations WHERE collection = %s AND branch_id = %s',
                    (collection, target_branch_id)
                )

                cur.execute(
                    'SELECT record_id, field_name, related_collection, related_id '
                    'FROM project_version_relations WHERE version_id = %s AND collection = %s',
                    (version_id, collection)
                )
                rel_rows = cur.fetchall()
                for record_id, field_name, related_coll, related_id in rel_rows:
                    cur.execute(
                        'INSERT INTO data_relations '
                        '(collection, record_id, field_name, related_collection, related_id, branch_id) '
                        'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                        (collection, record_id, field_name, related_coll, related_id, target_branch_id)
                    )

            merge_result['collections'].append(coll_result)

        # 更新版本状态
        cur.execute(
            'UPDATE project_versions SET status = %s, merged_at = %s, merged_by = %s WHERE id = %s',
            ('merged', now, merged_by, version_id)
        )

    return merge_result


def restore_from_project_version(version_id, restored_by, user_id, project_menu_id):
    """
    从项目版本恢复数据（完全覆盖当前分支数据）

    Parameters
    ----------
    version_id : str
        版本 ID
    restored_by : str
        恢复者
    user_id : str
        用户 ID
    project_menu_id : str
        项目菜单 ID

    Returns
    -------
    dict
        {success, recordsCount, relationsCount}
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 获取版本信息
        cur.execute(
            'SELECT name, records_count FROM project_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')

        # 获取用户当前分支
        current_branch = get_user_project_branch(user_id, project_menu_id)

        # 获取项目下所有 Collection
        collections = get_project_collections(project_menu_id)

        total_records = 0
        total_relations = 0

        for coll_info in collections:
            collection = coll_info['collection']

            # 清空当前分支数据
            cur.execute(
                'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (collection, current_branch)
            )
            cur.execute(
                'DELETE FROM data_relations WHERE collection = %s AND branch_id = %s',
                (collection, current_branch)
            )

            # 从快照恢复数据
            cur.execute(
                'SELECT record_id, record_data, created_at FROM project_version_snapshots '
                'WHERE version_id = %s AND collection = %s',
                (version_id, collection)
            )
            snapshot_rows = cur.fetchall()

            for record_id, data, created_at in snapshot_rows:
                cur.execute(
                    'INSERT INTO dynamic_data (id, collection, data, branch_id, created_at) '
                    'VALUES (%s, %s, %s, %s, %s)',
                    (record_id, collection, psycopg2.extras.Json(data), current_branch, created_at)
                )
                total_records += 1

            # 恢复关联数据
            cur.execute(
                'SELECT record_id, field_name, related_collection, related_id '
                'FROM project_version_relations WHERE version_id = %s AND collection = %s',
                (version_id, collection)
            )
            rel_rows = cur.fetchall()

            for record_id, field_name, related_coll, related_id in rel_rows:
                cur.execute(
                    'INSERT INTO data_relations '
                    '(collection, record_id, field_name, related_collection, related_id, branch_id) '
                    'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                    (collection, record_id, field_name, related_coll, related_id, current_branch)
                )
                total_relations += 1

    return {
        'success': True,
        'recordsCount': total_records,
        'relationsCount': total_relations,
    }


def switch_to_main_project_branch(user_id, username, project_menu_id):
    """
    切换项目到主分支

    Parameters
    ----------
    user_id : str
        用户 ID
    username : str
        用户名
    project_menu_id : str
        项目菜单 ID

    Returns
    -------
    dict
        {branchId, branchName, affectedCollections}
    """
    from utils.version import set_user_current_branch

    # 获取项目下所有 Collection
    collections = get_project_collections(project_menu_id)

    # 更新用户项目分支状态
    set_user_project_branch(user_id, username, project_menu_id, MAIN_BRANCH_ID)

    # 同步更新所有 Collection 的分支状态
    for coll_info in collections:
        set_user_current_branch(user_id, username, coll_info['collection'], MAIN_BRANCH_ID)

    return {
        'branchId': MAIN_BRANCH_ID,
        'branchName': '主分支',
        'affectedCollections': [c['collection'] for c in collections],
        'collectionsCount': len(collections),
    }


def get_project_version_delete_impact(version_id):
    """
    获取删除项目版本的影响报告

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    dict
        {
            'versionInfo': {...},
            'collections': [...],
            'usersOnBranch': [...],
            'hasChildVersions': bool,
            'warningMessage': str
        }
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 获取版本基本信息
        cur.execute(
            'SELECT id, project_menu_id, name, version_type, status, records_count, is_protected '
            'FROM project_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')

        version_info = {
            'id': row[0],
            'projectMenuId': row[1],
            'name': row[2],
            'versionType': row[3],
            'status': row[4],
            'recordsCount': row[5],
            'isProtected': row[6],
        }

        project_menu_id = row[1]

        # 获取项目下所有 Collection
        collections = get_project_collections(project_menu_id)

        # 统计每个 Collection 的快照数据
        affected_collections = []
        for coll_info in collections:
            collection = coll_info['collection']
            cur.execute(
                'SELECT COUNT(*) FROM project_version_snapshots '
                'WHERE version_id = %s AND collection = %s',
                (version_id, collection)
            )
            count = cur.fetchone()[0]

            # 获取记录详情（前100条）
            cur.execute(
                'SELECT record_id, record_data FROM project_version_snapshots '
                'WHERE version_id = %s AND collection = %s LIMIT 100',
                (version_id, collection)
            )
            records = []
            for rec_row in cur.fetchall():
                record_id = rec_row[0]
                data = rec_row[1] or {}
                display_name = (
                    data.get('name') or
                    data.get('title') or
                    data.get('caseName') or
                    data.get('planName') or
                    record_id
                )
                records.append({
                    'id': record_id,
                    'displayName': display_name,
                })

            affected_collections.append({
                'collection': collection,
                'pageName': coll_info['pageName'],
                'recordCount': count,
                'records': records,
                'hasMore': count > 100,
            })

        # 检查是否有子版本
        cur.execute(
            'SELECT COUNT(*) FROM project_versions WHERE parent_version = %s',
            (version_id,)
        )
        child_count = cur.fetchone()[0]

        # 检查是否有用户在此分支
        cur.execute(
            'SELECT username FROM user_current_project_branch '
            'WHERE project_menu_id = %s AND branch_id = %s',
            (project_menu_id, version_id)
        )
        users_on_branch = [row[0] for row in cur.fetchall()]

        # 生成警告信息
        if version_info['isProtected']:
            warning_msg = '该版本受保护，无法删除'
        elif child_count > 0:
            warning_msg = f'存在 {child_count} 个子版本，无法删除'
        elif users_on_branch:
            warning_msg = f'当前有 {len(users_on_branch)} 位用户正在使用此分支：{", ".join(users_on_branch)}'
        else:
            total_records = sum(c['recordCount'] for c in affected_collections)
            warning_msg = f'将删除 {len(collections)} 个 Collection 共 {total_records} 条快照数据'

        return {
            'versionInfo': version_info,
            'collections': affected_collections,
            'usersOnBranch': users_on_branch,
            'hasChildVersions': child_count > 0,
            'childCount': child_count,
            'warningMessage': warning_msg,
            'canDelete': not version_info['isProtected'] and child_count == 0,
        }