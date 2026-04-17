"""
版本管理核心逻辑

职责：
- 创建集合版本快照
- 对比两个版本差异
- 合并版本到当前数据
- 从版本恢复数据
- 分支管理（数据分支化支持）
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from db import get_db
import psycopg2.extras
from utils.errors import MergeError, VERSION_NOT_FOUND, VERSION_ALREADY_MERGED
from utils.version_scan import scan_all_related_data


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime objects"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


# ==================== 分支管理函数 ====================

# 主分支的 branch_id 常量
MAIN_BRANCH_ID = 'main'


def get_user_current_branch(user_id, collection):
    """
    获取用户在指定集合的当前工作分支

    Parameters
    ----------
    user_id : str
        用户 ID
    collection : str
        集合名称

    Returns
    -------
    str
        当前分支 ID，'main' 表示主分支
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT branch_id FROM user_current_branch WHERE user_id = %s AND collection = %s',
            (user_id, collection),
        )
        row = cur.fetchone()
        if row:
            return row[0] or MAIN_BRANCH_ID  # 返回 'main' 如果为 NULL
        return MAIN_BRANCH_ID  # 未设置，默认主分支


def set_user_current_branch(user_id, username, collection, branch_id):
    """
    设置用户在指定集合的当前工作分支

    Parameters
    ----------
    user_id : str
        用户 ID
    username : str
        用户名
    collection : str
        集合名称
    branch_id : str
        分支 ID，'main' 表示切换到主分支
    """
    now = datetime.now(timezone.utc)
    record_id = f'ucb-{user_id}-{collection}'
    actual_branch_id = branch_id or MAIN_BRANCH_ID

    with get_db() as conn:
        cur = conn.cursor()
        # 使用 upsert
        cur.execute(
            'INSERT INTO user_current_branch (id, user_id, username, collection, branch_id, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, %s) '
            'ON CONFLICT (user_id, collection) DO UPDATE SET branch_id = %s, updated_at = %s',
            (record_id, user_id, username, collection, actual_branch_id, now, actual_branch_id, now),
        )


def clear_user_current_branch(user_id, collection):
    """
    清除用户在指定集合的当前分支设置（切换回主分支）

    Parameters
    ----------
    user_id : str
        用户 ID
    collection : str
        集合名称
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE user_current_branch SET branch_id = %s, updated_at = %s '
            'WHERE user_id = %s AND collection = %s',
            (MAIN_BRANCH_ID, datetime.now(timezone.utc), user_id, collection),
        )


def copy_data_to_branch(collection, source_branch_id, target_branch_id):
    """
    复制一个分支的数据到另一个分支

    Parameters
    ----------
    collection : str
        集合名称
    source_branch_id : str
        源分支 ID，'main' 表示主分支
    target_branch_id : str
        目标分支 ID
    """
    source_branch = source_branch_id or MAIN_BRANCH_ID

    with get_db() as conn:
        cur = conn.cursor()

        # 复制 dynamic_data
        cur.execute(
            "INSERT INTO dynamic_data (id, collection, data, created_at, updated_at, version, branch_id) "
            "SELECT id, collection, data, created_at, updated_at, version, %s "
            "FROM dynamic_data WHERE collection = %s AND branch_id = %s "
            "ON CONFLICT (id, branch_id) DO NOTHING",
            (target_branch_id, collection, source_branch),
        )

        # 复制 data_relations
        cur.execute(
            "INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) "
            "SELECT collection, record_id, field_name, related_collection, related_id, %s "
            "FROM data_relations WHERE collection = %s AND branch_id = %s "
            "ON CONFLICT DO NOTHING",
            (target_branch_id, collection, source_branch),
        )


def get_branch_data_count(collection, branch_id):
    """
    获取分支的数据记录数量

    Parameters
    ----------
    collection : str
        集合名称
    branch_id : str
        分支 ID，'main' 表示主分支

    Returns
    -------
    int
        记录数量
    """
    actual_branch_id = branch_id or MAIN_BRANCH_ID
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection, actual_branch_id),
        )
        return cur.fetchone()[0]


def get_users_on_branch(collection, branch_id):
    """
    获取正在使用指定collection的指定分支的用户列表

    Parameters
    ----------
    collection : str
        集合名称
    branch_id : str
        分支版本 ID

    Returns
    -------
    List[str]
        用户名列表
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT username FROM user_current_branch '
            'WHERE collection = %s AND branch_id = %s',
            (collection, branch_id)
        )
        return [row[0] for row in cur.fetchall()]


def get_version_collections(version_id):
    """
    获取版本涉及的所有collections

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    List[str]
        所有参与的collection列表
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s',
            (version_id,)
        )
        return [row[0] for row in cur.fetchall()]


def get_version_collection_stats(version_id):
    """
    获取每个collection的记录数统计

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    Dict[str, int]
        {collection: records_count}
    """
    stats = {}
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection, COUNT(*) FROM version_snapshots '
            'WHERE version_id = %s GROUP BY collection',
            (version_id,)
        )
        for row in cur.fetchall():
            stats[row[0]] = row[1]
    return stats


def get_primary_collection(version_id):
    """
    获取版本的主collection（创建点）

    Primary collection的parent_version不自引用（null或指向其他版本）
    Non-primary collection的parent_version = version_id（自引用）

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    str | None
        主collection名称，未找到返回None
    """
    with get_db() as conn:
        cur = conn.cursor()
        # Primary collection is stored in collection_versions.collection
        cur.execute(
            'SELECT collection FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        return row[0] if row else None


def initialize_branch_from_snapshot(version_id, affected_collections, cur):
    """
    从快照初始化分支数据（首次切换场景）

    该函数执行以下操作：
    1. 清空目标分支的所有collection数据（避免重复）
    2. 从version_snapshots读取并插入每个collection的数据
    3. 从version_relations恢复每个collection的关联数据
    4. 返回初始化的记录总数

    Parameters
    ----------
    version_id : str
        目标版本 ID
    affected_collections : List[str]
        需要初始化的collection列表
    cur : cursor
        数据库游标（必须在事务中）

    Returns
    -------
    int
        初始化的记录总数

    Raises
    -------
    ValueError
        如果某个collection缺少快照数据
    """
    # 1. 清空目标分支的所有collection数据
    for coll in affected_collections:
        cur.execute(
            'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (coll, version_id)
        )

    # 2. 读取所有collection的快照数据
    cur.execute(
        'SELECT collection, record_id, record_data, created_at FROM version_snapshots WHERE version_id = %s',
        (version_id,),
    )
    target_records = cur.fetchall()

    # 3. 验证所有collection都有快照数据
    snapshot_collections = {row[0] for row in target_records}
    missing_snapshots = set(affected_collections) - snapshot_collections
    if missing_snapshots:
        raise ValueError(
            f'无法初始化版本：以下 Collection 缺少快照数据: {sorted(missing_snapshots)}。'
            f'该版本可能在多 Collection 支持完善前创建，快照数据不完整。'
        )

    # 4. 读取所有关联数据
    cur.execute(
        'SELECT collection, record_id, field_name, related_collection, related_id '
        'FROM version_relations WHERE version_id = %s',
        (version_id,),
    )
    target_relations = cur.fetchall()

    # 5. 按 collection 分组插入数据
    records_by_collection = {}
    for coll, rid, data, created_at in target_records:
        if coll not in records_by_collection:
            records_by_collection[coll] = []
        records_by_collection[coll].append((rid, data, created_at))

    # 6. 插入每个 collection 的数据
    total_inserted = 0
    for coll, records in records_by_collection.items():
        for rid, data, created_at in records:
            flat_data = {k: v for k, v in data.items()} if isinstance(data, dict) else {}
            cur.execute(
                'INSERT INTO dynamic_data (id, collection, data, branch_id, created_at, version) '
                'VALUES (%s, %s, %s, %s, %s, %s)',
                (rid, coll, psycopg2.extras.Json(flat_data), version_id, created_at, 1),
            )
            total_inserted += 1

    # 7. 恢复所有 collection 的关联数据
    for coll in affected_collections:
        _replace_collection_relations(cur, coll, version_id, target_relations)

    return total_inserted


def count_collection_relations(collection, branch_id, cur):
    """
    统计collection在指定分支的关联数量

    Parameters
    ----------
    collection : str
        Collection名称
    branch_id : str
        分支 ID
    cur : cursor
        数据库游标（避免多次创建连接）

    Returns
    -------
    int
        关联数量
    """
    cur.execute(
        'SELECT COUNT(*) FROM data_relations '
        'WHERE collection = %s AND branch_id = %s',
        (collection, branch_id)
    )
    return cur.fetchone()[0]


def _compute_data_hash(records, relations):
    """计算数据和关联的 SHA256 哈希，用于快速判断数据是否相同"""
    data_str = json.dumps(records, sort_keys=True, ensure_ascii=False, cls=DateTimeEncoder)
    rel_str = json.dumps(relations, sort_keys=True, ensure_ascii=False, cls=DateTimeEncoder)
    combined = data_str + rel_str
    return hashlib.sha256(combined.encode()).hexdigest()


def create_version_snapshot(collection, name, description, version_type, parent_version, created_by, branch_id=None):
    """
    创建集合版本快照

    Parameters
    ----------
    collection : str
        集合名称
    name : str
        版本名称
    description : str
        版本描述
    version_type : str
        'snapshot' 或 'branch'
    parent_version : str | None
        父版本 ID
    created_by : str
        创建者
    branch_id : str | None
        要快照的分支 ID，None 表示主分支

    Returns
    -------
    dict
        版本信息
    """
    version_id = f'ver-{uuid.uuid4().hex[:8]}'
    now = datetime.now(timezone.utc)
    actual_branch_id = branch_id or MAIN_BRANCH_ID

    with get_db() as conn:
        cur = conn.cursor()

        # 1. Recursively scan all related collections' data
        try:
            all_collections_data = scan_all_related_data(
                start_collection=collection,
                branch_id=actual_branch_id,
                max_records=10000
            )
        except ValueError as e:
            conn.rollback()
            raise ValueError(f'Failed to create version: {str(e)}')

        # 2. Query relations from ALL collections
        all_relations = []
        for coll in all_collections_data.keys():
            cur.execute(
                'SELECT collection, record_id, field_name, related_collection, related_id '
                'FROM data_relations WHERE collection = %s AND branch_id = %s',
                (coll, actual_branch_id),
            )
            all_relations.extend(cur.fetchall())

        # 3. Calculate hash and counts
        records_count = sum(len(records) for records in all_collections_data.values())
        relations_count = len(all_relations)
        data_hash = _compute_data_hash(all_collections_data, all_relations)

        # 4. Insert version metadata
        cur.execute(
            'INSERT INTO collection_versions '
            '(id, collection, name, description, version_type, parent_version, status, '
            'data_hash, records_count, relations_count, created_by, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (version_id, collection, name, description, version_type, parent_version,
             'active', data_hash, records_count, relations_count, created_by, now),
        )

        # 5. Insert all collections' data to version_snapshots
        for coll, records in all_collections_data.items():
            if records:
                snapshot_values = [
                    (version_id, coll, record['id'], psycopg2.extras.Json(record['data']), record['created_at'])
                    for record in records
                ]
                psycopg2.extras.execute_values(
                    cur,
                    'INSERT INTO version_snapshots (version_id, collection, record_id, record_data, created_at) '
                    'VALUES %s',
                    snapshot_values,
                )

        # 6. Insert all relations to version_relations
        if all_relations:
            rel_values = [
                (version_id, r[0], r[1], r[2], r[3], r[4])
                for r in all_relations
            ]
            psycopg2.extras.execute_values(
                cur,
                'INSERT INTO version_relations '
                '(version_id, collection, record_id, field_name, related_collection, related_id) '
                'VALUES %s',
                rel_values,
            )

        # 7. Track version涉及的所有 Collection（用于跨 Collection 分支切换）
        # 传递实际的 Collection 列表，避免数据库扫描
        track_version_collections(
            version_id,
            collection,
            actual_branch_id,
            conn,
            list(all_collections_data.keys())  # 显式传递实际参与的 Collection
        )

    return {
        'id': version_id,
        'collection': collection,
        'name': name,
        'description': description,
        'versionType': version_type,
        'parentVersion': parent_version,
        'status': 'active',
        'dataHash': data_hash,
        'recordsCount': records_count,
        'relationsCount': relations_count,
        'createdBy': created_by,
        'createdAt': now.isoformat(),
        'affectedCollections': list(all_collections_data.keys()),
    }


def track_version_collections(version_id, collection, branch_id, conn=None, affected_collections=None):
    """
    追踪版本涉及的所有 Collection

    Parameters
    ----------
    version_id : str
        版本 ID
    collection : str
        版本创建时的主 Collection
    branch_id : str
        分支 ID
    conn : connection, optional
        已有的数据库连接，如果提供则复用（用于事务内调用）
    affected_collections : list, optional
        实际参与版本的 Collection 列表。如果提供，则直接使用；否则扫描数据库（向后兼容）
    """
    now = datetime.now(timezone.utc)

    # 复用现有连接或创建新连接
    if conn:
        cur = conn.cursor()
        _track_collections_internal(cur, version_id, collection, branch_id, now, affected_collections)
    else:
        with get_db() as conn:
            cur = conn.cursor()
            _track_collections_internal(cur, version_id, collection, branch_id, now, affected_collections)


def _track_collections_internal(cur, version_id, collection, branch_id, now, affected_collections=None):
    """
    内部追踪逻辑（共享游标）

    Parameters
    ----------
    cur : cursor
        数据库游标
    version_id : str
        版本 ID
    collection : str
        版本创建时的主 Collection
    branch_id : str
        分支 ID
    now : datetime
        当前时间戳
    affected_collections : list, optional
        实际参与版本的 Collection 列表。如果提供，则直接使用；否则扫描数据库（向后兼容）
    """
    # 如果提供了显式列表，直接使用（避免数据库扫描）
    if affected_collections is not None:
        all_collections = set(affected_collections)
        # 如果列表为空，至少记录主 Collection
        if not all_collections:
            all_collections = {collection}
    else:
        # 向后兼容：扫描数据库获取所有涉及的 Collection
        # 1. 扫描直接数据（dynamic_data）
        cur.execute(
            'SELECT DISTINCT collection FROM dynamic_data WHERE branch_id = %s',
            (branch_id,)
        )
        direct_collections = [row[0] for row in cur.fetchall()]

        # 2. 扫描关联数据（data_relations）- 源和目标Collection
        cur.execute(
            'SELECT DISTINCT collection FROM data_relations WHERE branch_id = %s '
            'UNION '
            'SELECT DISTINCT related_collection FROM data_relations WHERE branch_id = %s',
            (branch_id, branch_id)
        )
        relation_collections = [row[0] for row in cur.fetchall()]

        # 3. 合并去重
        all_collections = set(direct_collections + relation_collections)

        # 如果没有任何数据，至少记录主Collection
        if not all_collections:
            all_collections = {collection}

    # 4. 插入追踪数据
    for coll in all_collections:
        cur.execute(
            'INSERT INTO version_collections (version_id, collection, created_at) '
            'VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
            (version_id, coll, now)
        )


def get_version_delete_impact(version_id):
    """
    获取删除版本的影响范围报告

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    dict
        {
            'versionInfo': {...},
            'affectedCollections': [...],
            'usersOnBranch': [...],
            'totalRecords': N,
            'totalRelations': M,
            'hasCrossCollectionData': bool,
            'hasUsersOnBranch': bool,
            'warningMessage': str
        }
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 1. 获取版本基本信息
        cur.execute(
            'SELECT id, name, collection, version_type, records_count, relations_count '
            'FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')

        version_info = {
            'id': row[0],
            'name': row[1],
            'collection': row[2],
            'versionType': row[3],
            'recordsCount': row[4],
            'relationsCount': row[5]
        }

        # 2. 查询涉及的 Collection
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s ORDER BY collection',
            (version_id,)
        )
        collections = [row[0] for row in cur.fetchall()]

        # 3. 查询每个 Collection 的数据详情
        affected_collections = []
        for coll in collections:
            # 统计总数
            cur.execute(
                'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (coll, version_id)
            )
            total_count = cur.fetchone()[0]

            # 查询数据详情（前100条）
            cur.execute(
                'SELECT id, data, created_at, updated_at '
                'FROM dynamic_data '
                'WHERE collection = %s AND branch_id = %s '
                'ORDER BY created_at DESC LIMIT 100',
                (coll, version_id)
            )
            data_rows = cur.fetchall()

            records = []
            for data_row in data_rows:
                record_id = data_row[0]
                data_json = data_row[1] or {}

                display_name = (
                    data_json.get('name') or
                    data_json.get('title') or
                    data_json.get('caseName') or
                    data_json.get('planName') or
                    record_id
                )

                records.append({
                    'id': record_id,
                    'displayName': display_name,
                    'createdAt': data_row[2].isoformat() if data_row[2] else None,
                    'updatedAt': data_row[3].isoformat() if data_row[3] else None
                })

            affected_collections.append({
                'collection': coll,
                'recordCount': total_count,
                'records': records,
                'hasMore': total_count > 100
            })

        # 4. 统计关联关系数量
        cur.execute(
            'SELECT COUNT(*) FROM data_relations WHERE branch_id = %s',
            (version_id,)
        )
        total_relations = cur.fetchone()[0]

        # 5. 获取当前使用该分支的用户（遍历所有collection）
        users_on_branch = []
        for coll in collections:
            usernames = get_users_on_branch(coll, version_id)
            for username in usernames:
                users_on_branch.append({
                    'username': username,
                    'collection': coll
                })
        has_users = len(users_on_branch) > 0

        # 6. 生成警告信息
        has_cross = len(collections) > 1
        warning_msg = ''
        if len(affected_collections) == 0:
            # 版本无追踪数据（可能创建后未调用 track_version_collections）
            warning_msg = '该版本暂无追踪数据，建议先运行数据迁移脚本'
        elif has_users:
            user_list = ', '.join([f"{u['username']}({u['collection']})" for u in users_on_branch])
            warning_msg = (
                f'当前有 {len(users_on_branch)} 位用户正在使用此分支：{user_list}\n'
                f'删除将强制清理这些用户的分支设置。'
            )
        elif has_cross:
            collection_list = ', '.join([
                f"{item['collection']}({item['recordCount']}条)"
                for item in affected_collections
            ])
            warning_msg = (
                f'该版本涉及 {len(collections)} 个 Collection 的数据：\n'
                f'{collection_list}\n'
                f'删除将同时清理这些数据及 {total_relations} 条关联关系。'
            )
        else:
            warning_msg = f'将删除 {affected_collections[0]["collection"]} 的 {affected_collections[0]["recordCount"]} 条数据'

        return {
            'versionInfo': version_info,
            'affectedCollections': affected_collections,
            'usersOnBranch': users_on_branch,
            'totalRecords': sum(item['recordCount'] for item in affected_collections),
            'totalRelations': total_relations,
            'hasCrossCollectionData': has_cross,
            'hasUsersOnBranch': has_users,
            'warningMessage': warning_msg
        }


def _load_relation_field_map(cur, collection):
    """Load relation field definitions for a collection."""
    page_id = f'page-{collection}'
    cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
    try:
        row = cur.fetchone()
    except StopIteration:
        return {}

    fields = row[0] if row and isinstance(row[0], list) else []

    relation_map = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        if field.get('controlType') != 'relation':
            continue
        field_name = field.get('fieldName')
        relation_config = field.get('relationConfig') or {}
        if not field_name:
            continue
        relation_map[field_name] = {
            'target_collection': relation_config.get('targetCollection'),
            'target_field': relation_config.get('targetField'),
        }
    return relation_map


def _replace_collection_relations(cur, collection, branch_id, relation_rows):
    """Replace a collection's forward relations and rebuild reverse rows."""
    relation_map = _load_relation_field_map(cur, collection)

    cur.execute(
        'DELETE FROM data_relations WHERE collection = %s AND branch_id = %s',
        (collection, branch_id),
    )

    managed_reverse_fields = {
        (cfg['target_collection'], cfg['target_field'])
        for cfg in relation_map.values()
        if cfg.get('target_collection') and cfg.get('target_field')
    }
    for target_collection, target_field in managed_reverse_fields:
        cur.execute(
            'DELETE FROM data_relations '
            'WHERE collection = %s AND field_name = %s AND related_collection = %s AND branch_id = %s',
            (target_collection, target_field, collection, branch_id),
        )

    for rel_collection, record_id, field_name, related_collection, related_id in relation_rows:
        cur.execute(
            'INSERT INTO data_relations '
            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
            (rel_collection, record_id, field_name, related_collection, related_id, branch_id),
        )

        field_config = relation_map.get(field_name) or {}
        target_collection = field_config.get('target_collection')
        target_field = field_config.get('target_field')
        if not target_collection or not target_field:
            continue
        if target_collection != related_collection:
            continue

        cur.execute(
            'INSERT INTO data_relations '
            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
            (target_collection, related_id, target_field, collection, record_id, branch_id),
        )


def _load_collection_relation_rows(cur, collection, branch_id):
    """Load forward relation rows for one collection and branch."""
    cur.execute(
        'SELECT collection, record_id, field_name, related_collection, related_id '
        'FROM data_relations WHERE collection = %s AND branch_id = %s',
        (collection, branch_id),
    )
    return cur.fetchall()


def get_version_list(collection=None, status=None, page=None, pageSize=None, keyword=None):
    """
    获取版本列表

    Parameters
    ----------
    collection : str | None
        筛选集合，None 表示所有集合
    status : str | None
        筛选状态，None 表示所有状态
    page : int | None
        页码（从1开始），提供时启用分页
    pageSize : int | None
        每页数量，与 page 配合使用
    keyword : str | None
        关键词搜索，匹配 name 或 description

    Returns
    -------
    list[dict] | dict
        无分页时返回版本列表
        有分页时返回 {items: list, total: int}
    """
    with get_db() as conn:
        cur = conn.cursor()
        conditions = []
        params = []

        if collection:
            conditions.append('collection = %s')
            params.append(collection)
        if status:
            conditions.append('status = %s')
            params.append(status)
        if keyword:
            conditions.append('(name ILIKE %s OR description ILIKE %s)')
            keyword_pattern = f'%{keyword}%'
            params.extend([keyword_pattern, keyword_pattern])

        where_clause = 'WHERE ' + ' AND '.join(conditions) if conditions else ''

        # Validate pagination parameters
        if page is not None:
            if page < 1:
                page = 1  # Normalize invalid page to first page
            if pageSize is None:
                pageSize = 10  # Default when only page provided
            if pageSize < 1:
                pageSize = 10  # Default for invalid pageSize

        # When pagination is requested, first get total count
        if page is not None and pageSize is not None:
            count_sql = f'SELECT COUNT(*) FROM collection_versions {where_clause}'
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]

            # Then get paginated results
            offset = (page - 1) * pageSize
            sql = f'''
                SELECT id, collection, name, description, version_type, parent_version, status,
                       data_hash, records_count, relations_count, created_by, created_at,
                       merged_at, merged_by, merged_into, is_protected
                FROM collection_versions
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            '''
            cur.execute(sql, params + [pageSize, offset])
            rows = cur.fetchall()

            items = [{
                'id': r[0],
                'collection': r[1],
                'name': r[2],
                'description': r[3],
                'versionType': r[4],
                'parentVersion': r[5],
                'status': r[6],
                'dataHash': r[7],
                'recordsCount': r[8],
                'relationsCount': r[9],
                'createdBy': r[10],
                'createdAt': r[11].isoformat() if r[11] else None,
                'mergedAt': r[12].isoformat() if r[12] else None,
                'mergedBy': r[13],
                'mergedInto': r[14],
                'isProtected': r[15],
            } for r in rows]

            return {'items': items, 'total': total}

        # Non-paginated query (backward compatible)
        sql = f'''
            SELECT id, collection, name, description, version_type, parent_version, status,
                   data_hash, records_count, relations_count, created_by, created_at,
                   merged_at, merged_by, merged_into, is_protected
            FROM collection_versions
            {where_clause}
            ORDER BY created_at DESC
        '''
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [{
        'id': r[0],
        'collection': r[1],
        'name': r[2],
        'description': r[3],
        'versionType': r[4],
        'parentVersion': r[5],
        'status': r[6],
        'dataHash': r[7],
        'recordsCount': r[8],
        'relationsCount': r[9],
        'createdBy': r[10],
        'createdAt': r[11].isoformat() if r[11] else None,
        'mergedAt': r[12].isoformat() if r[12] else None,
        'mergedBy': r[13],
        'mergedInto': r[14],
        'isProtected': r[15],
    } for r in rows]


def get_version_detail(version_id):
    """
    获取版本详情

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    dict | None
        版本详情，不存在返回 None
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, collection, name, description, version_type, parent_version, status, '
            'data_hash, records_count, relations_count, created_by, created_at, '
            'merged_at, merged_by, merged_into, is_protected '
            'FROM collection_versions WHERE id = %s',
            (version_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

    return {
        'id': row[0],
        'collection': row[1],
        'name': row[2],
        'description': row[3],
        'versionType': row[4],
        'parentVersion': row[5],
        'status': row[6],
        'dataHash': row[7],
        'recordsCount': row[8],
        'relationsCount': row[9],
        'createdBy': row[10],
        'createdAt': row[11].isoformat() if row[11] else None,
        'mergedAt': row[12].isoformat() if row[12] else None,
        'mergedBy': row[13],
        'mergedInto': row[14],
        'isProtected': row[15],
    }


def delete_version(version_id, confirmed=False):
    """
    删除版本（改造版：支持用户确认机制）

    Parameters
    ----------
    version_id : str
        版本 ID
    confirmed : bool
        是否已确认删除（前端确认后传入 True）

    Returns
    -------
    dict | bool
        如果 confirmed=False，返回影响报告 dict
        如果 confirmed=True，返回删除成功 bool

    Raises
    ------
    ValueError
        如果有用户正在使用该分支，或版本受保护、有子版本
    """
    # 未确认：返回影响报告
    if not confirmed:
        return get_version_delete_impact(version_id)

    # 已确认：执行删除前的检查
    # 1. 获取所有参与的collections
    collections = get_version_collections(version_id)

    # 2. 检查是否有用户在使用（Critical fix）
    users_using = []
    for coll in collections:
        users = get_users_on_branch(coll, version_id)
        users_using.extend(users)

    if users_using:
        raise ValueError(
            f'有用户正在使用该分支：{", ".join(set(users_using))}。'
            f'请通知他们切换到其他分支后再删除。'
        )

    # 已确认且无用户使用：执行删除
    with get_db() as conn:
        cur = conn.cursor()

        # 3. 检查版本状态
        cur.execute(
            'SELECT is_protected, collection, version_type FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            return False
        if row[0]:
            raise ValueError('无法删除受保护的版本')
        collection = row[1]
        version_type = row[2]

        # 4. 检查子版本
        cur.execute(
            'SELECT COUNT(*) FROM collection_versions WHERE parent_version = %s',
            (version_id,)
        )
        child_count = cur.fetchone()[0]
        if child_count > 0:
            raise ValueError(f'无法删除：存在 {child_count} 个子版本')

        # 5. 如果是分支，精确清理数据
        if version_type == 'branch':
            # 查询涉及的 Collection
            cur.execute(
                'SELECT collection FROM version_collections WHERE version_id = %s',
                (version_id,)
            )
            db_collections = [row[0] for row in cur.fetchall()]

            # 如果无追踪数据，扫描 version_snapshots 获取所有涉及的 Collection
            # 兼容 Task 1-3 实施前创建的旧版本
            if not db_collections:
                cur.execute(
                    'SELECT DISTINCT collection FROM version_snapshots WHERE version_id = %s',
                    (version_id,)
                )
                snapshot_collections = [row[0] for row in cur.fetchall()]
                if snapshot_collections:
                    db_collections = snapshot_collections
                else:
                    # 最终 fallback：使用 metadata collection
                    db_collections = [collection]

            # 精确清理每个 Collection
            for coll in db_collections:
                cur.execute(
                    'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                    (coll, version_id)
                )

            # 清理关联关系
            cur.execute(
                'DELETE FROM data_relations WHERE branch_id = %s',
                (version_id,)
            )

            # 清理用户分支设置
            cur.execute(
                'DELETE FROM user_current_branch WHERE branch_id = %s',
                (version_id,)
            )

        # 6. 删除版本相关数据（显式删除，不依赖 CASCADE）
        # 删除 version_snapshots
        cur.execute('DELETE FROM version_snapshots WHERE version_id = %s', (version_id,))
        # 删除 version_relations
        cur.execute('DELETE FROM version_relations WHERE version_id = %s', (version_id,))
        # 删除 version_collections（CASCADE 应该也会删除，但显式删除更安全）
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        # 最后删除版本元数据
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))

    return True


def load_version_data(version_id):
    """
    加载版本数据

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    tuple
        (records, relations_map) 或 (None, None) 如果版本不存在
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 检查版本是否存在
        cur.execute('SELECT id FROM collection_versions WHERE id = %s', (version_id,))
        if not cur.fetchone():
            return None, None

        # 加载快照数据
        cur.execute(
            'SELECT record_id, record_data FROM version_snapshots WHERE version_id = %s',
            (version_id,),
        )
        snapshot_rows = cur.fetchall()

        # 加载关联数据
        cur.execute(
            'SELECT record_id, field_name, related_id FROM version_relations WHERE version_id = %s',
            (version_id,),
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


def load_current_data(collection, branch_id=None):
    """
    加载当前数据

    Parameters
    ----------
    collection : str
        集合名称
    branch_id : str | None
        分支 ID，None 或 'main' 表示主分支

    Returns
    -------
    tuple
        (records, relations_map)
    """
    actual_branch_id = branch_id or MAIN_BRANCH_ID
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, data FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection, actual_branch_id),
        )
        data_rows = cur.fetchall()

        cur.execute(
            'SELECT record_id, field_name, related_id FROM data_relations WHERE collection = %s AND branch_id = %s',
            (collection, actual_branch_id),
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


def compute_diff(base_records, target_records, field_names, base_rels=None, target_rels=None, relation_fields=None):
    """
    计算两组记录的差异

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


def merge_version_to_current(version_id, strategy, merged_by, user_id=None):
    """
    合并版本到当前数据

    Parameters
    ----------
    version_id : str
        源版本 ID
    strategy : str
        合并策略：'theirs'（使用源版本数据）或 'ours'（保留当前数据）
    merged_by : str
        合并者
    user_id : str | None
        用户 ID，用于获取目标分支

    Returns
    -------
    dict
        {success, summary: {recordsCreated, recordsUpdated, recordsDeleted}}
    """
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        cur = conn.cursor()

        # 获取版本信息
        cur.execute(
            'SELECT collection, status, version_type FROM collection_versions WHERE id = %s',
            (version_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')
        collection, status, version_type = row

        if status == 'merged':
            raise ValueError('该版本已被合并')

        # 获取目标分支（用户当前工作分支，如果没有则是主分支）
        target_branch_id = get_user_current_branch(user_id, collection) if user_id else MAIN_BRANCH_ID

        # 加载源数据
        if version_type == 'branch':
            # 对于分支类型，从 dynamic_data 加载（带 branch_id 过滤）
            source_records, source_rels = load_current_data(collection, branch_id=version_id)
        else:
            # 对于快照类型，从 version_snapshots 加载
            source_records, source_rels = load_version_data(version_id)

        # 加载目标数据
        target_records, target_rels = load_current_data(collection, branch_id=target_branch_id)

        # 获取字段配置
        page_id = f'page-{collection}'
        cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
        pc_row = cur.fetchone()
        fields = pc_row[0] if pc_row and pc_row[0] else []
        field_names = [f['fieldName'] for f in fields]
        relation_fields = [f for f in fields if f.get('controlType') == 'relation']

        # 计算差异
        diff = compute_diff(
            target_records, source_records, field_names,
            target_rels, source_rels, relation_fields
        )

        summary = {
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
                summary['recordsDeleted'] += 1

            # 2. 插入新增记录（带目标分支 ID）
            for rec in diff['added']:
                data = {k: v for k, v in rec.items() if k != 'id'}
                cur.execute(
                    'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
                    (rec['id'], collection, psycopg2.extras.Json(data), target_branch_id),
                )
                summary['recordsCreated'] += 1

            # 3. 更新修改记录
            for item in diff['modified']:
                new_data = {k: v for k, v in item['record'].items() if k != 'id'}
                cur.execute(
                    'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 '
                    'WHERE collection = %s AND id = %s AND branch_id = %s',
                    (psycopg2.extras.Json(new_data), collection, item['id'], target_branch_id),
                )
                summary['recordsUpdated'] += 1

            # 4. Rebuild forward and reverse relations from the source state.
            if version_type == 'branch':
                cur.execute(
                    'SELECT collection, record_id, field_name, related_collection, related_id '
                    'FROM data_relations WHERE collection = %s AND branch_id = %s',
                    (collection, version_id),
                )
            else:
                cur.execute(
                    'SELECT collection, record_id, field_name, related_collection, related_id '
                    'FROM version_relations WHERE version_id = %s',
                    (version_id,),
                )
            _replace_collection_relations(cur, collection, target_branch_id, cur.fetchall())

        elif strategy == 'ours':
            # 保留当前数据，不做任何修改
            pass

        else:
            raise ValueError(f'不支持的合并策略: {strategy}')

        # 标记版本为已合并
        cur.execute(
            'UPDATE collection_versions SET status = %s, merged_at = %s, merged_by = %s '
            'WHERE id = %s',
            ('merged', now, merged_by, version_id),
        )

    return {
        'success': True,
        'summary': summary,
    }


def apply_partial_merge(source_version_id, target_branch, decisions, merged_by):
    """
    部分合并：根据用户决策选择性合并记录

    Parameters
    ----------
    source_version_id : str
        源版本 ID
    target_branch : str
        目标分支 ID，'main' 表示主分支
    decisions : dict
        用户合并决策
        {
            'added_record_ids': list[str],      # 要新增的记录 ID
            'removed_record_ids': list[str],    # 要删除的记录 ID
            'modified_records': list[dict]      # 要修改的记录
                [{'record_id': str, 'field_values': dict}]
        }
    merged_by : str
        合并者用户名

    Returns
    -------
    dict
        {success, message, merged_count, summary}

    Raises
    ------
    MergeError
        版本不存在或已合并时抛出
    """
    now = datetime.now(timezone.utc)
    target_branch_id = target_branch or MAIN_BRANCH_ID

    with get_db() as conn:
        cur = conn.cursor()

        # 1. 获取版本信息并验证状态
        cur.execute(
            'SELECT collection, status, version_type FROM collection_versions WHERE id = %s',
            (source_version_id,),
        )
        row = cur.fetchone()
        if not row:
            raise MergeError(VERSION_NOT_FOUND, '源版本不存在')
        collection, status, version_type = row

        if status == 'merged':
            raise MergeError(VERSION_ALREADY_MERGED, '该版本已合并，无法再次合并')

        # 2. 加载源数据（支持分支和快照两种类型）
        if version_type == 'branch':
            source_records, source_rels = load_current_data(collection, branch_id=source_version_id)
        else:
            source_records, source_rels = load_version_data(source_version_id)
            # load_version_data 返回 (records, relations_map)，需要转换格式
            # relations_map: {record_id: {field_name: [related_ids]}}
            # 我们需要将其转换为列表格式

        # 构建源记录查找表
        source_record_map = {r['id']: r for r in source_records}

        summary = {
            'recordsCreated': 0,
            'recordsDeleted': 0,
            'recordsUpdated': 0,
            'recordsSkipped': 0,      # 因冲突被跳过的记录
            'recordsNotFound': 0,     # 修改时未找到的目标记录
        }

        # 3. 处理新增记录
        added_record_ids = decisions.get('added_record_ids', [])
        for record_id in added_record_ids:
            if record_id not in source_record_map:
                summary['recordsSkipped'] += 1
                continue

            record = source_record_map[record_id]
            data = {k: v for k, v in record.items() if k != 'id'}

            # Insert by the branch-aware natural key.
            cur.execute(
                'INSERT INTO dynamic_data (id, collection, data, branch_id) '
                'VALUES (%s, %s, %s, %s) ON CONFLICT (id, branch_id) DO NOTHING',
                (record_id, collection, psycopg2.extras.Json(data), target_branch_id),
            )
            if cur.rowcount == 0:
                # 插入冲突，记录跳过
                summary['recordsSkipped'] += 1
                continue

            # 同步复制关系数据
            if isinstance(source_rels, list):
                # source_rels 是关系列表
                for rel in source_rels:
                    if isinstance(rel, dict) and rel.get('record_id') == record_id:
                        related_collection = rel.get('related_collection', collection)
                        cur.execute(
                            'INSERT INTO data_relations '
                            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
                            'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                            (collection, record_id, rel['field_name'],
                             related_collection, rel['related_id'], target_branch_id),
                        )
            elif isinstance(source_rels, dict):
                # source_rels 是关系映射 {record_id: {field_name: [related_ids]}}
                # 注意：此格式缺少 related_collection 信息，默认使用 collection
                if record_id in source_rels:
                    for field_name, related_ids in source_rels[record_id].items():
                        for related_id in related_ids:
                            cur.execute(
                                'INSERT INTO data_relations '
                                '(collection, record_id, field_name, related_collection, related_id, branch_id) '
                                'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                                (collection, record_id, field_name, collection, related_id, target_branch_id),
                            )

            summary['recordsCreated'] += 1

        # 4. 处理删除记录
        removed_record_ids = decisions.get('removed_record_ids', [])
        for record_id in removed_record_ids:
            # 删除记录
            cur.execute(
                'DELETE FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                (collection, record_id, target_branch_id),
            )
            deleted = cur.rowcount > 0

            # 同步删除关系数据
            cur.execute(
                'DELETE FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                (collection, record_id, target_branch_id),
            )

            if deleted:
                summary['recordsDeleted'] += 1
            else:
                summary['recordsNotFound'] += 1

        # 5. 处理修改记录
        modified_records = decisions.get('modified_records', [])

        # 加载关联字段配置，区分普通字段和关联字段
        relation_field_map = _load_relation_field_map(cur, collection)

        for mod in modified_records:
            record_id = mod.get('record_id')
            field_values = mod.get('field_values', {})
            relation_changes = mod.get('relation_changes', {})  # 新增：关联字段变更

            if not record_id:
                summary['recordsSkipped'] += 1
                continue

            # 分离普通字段和关联字段
            regular_field_values = {}
            relation_field_values = {}

            for fn, val in field_values.items():
                if fn in relation_field_map:
                    relation_field_values[fn] = val  # 关联字段的 ID 列表
                else:
                    regular_field_values[fn] = val  # 普通字段

            # 获取当前记录数据
            cur.execute(
                'SELECT data FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                (collection, record_id, target_branch_id),
            )
            current_row = cur.fetchone()
            if current_row:
                current_data = current_row[0] or {}

                # 仅更新普通字段到 data JSONB
                current_data.update(regular_field_values)
                cur.execute(
                    'UPDATE dynamic_data SET data = %s, updated_at = %s, version = version + 1 '
                    'WHERE collection = %s AND id = %s AND branch_id = %s',
                    (psycopg2.extras.Json(current_data), now, collection, record_id, target_branch_id),
                )

                # 处理关联字段变更
                all_relation_changes = relation_field_values.copy()
                all_relation_changes.update(relation_changes)  # 合并显式的 relation_changes

                for field_name, related_ids in all_relation_changes.items():
                    field_config = relation_field_map.get(field_name, {})
                    target_collection = field_config.get('target_collection', collection)
                    target_field = field_config.get('target_field')

                    if isinstance(related_ids, list):
                        related_ids_set = set(related_ids)
                    else:
                        related_ids_set = set([related_ids]) if related_ids else set()

                    # 删除该字段的所有现有关联（正向）
                    cur.execute(
                        'DELETE FROM data_relations WHERE collection = %s AND record_id = %s AND field_name = %s AND branch_id = %s',
                        (collection, record_id, field_name, target_branch_id),
                    )

                    # 删除反向关联（如果配置了 target_field）
                    if target_field:
                        cur.execute(
                            'DELETE FROM data_relations WHERE collection = %s AND field_name = %s AND related_collection = %s AND related_id = %s AND branch_id = %s',
                            (target_collection, target_field, collection, record_id, target_branch_id),
                        )

                    # 插入新关联
                    for related_id in related_ids_set:
                        cur.execute(
                            'INSERT INTO data_relations '
                            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
                            'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                            (collection, record_id, field_name, target_collection, related_id, target_branch_id),
                        )

                        # 插入反向关联（如果配置了 target_field）
                        if target_field:
                            cur.execute(
                                'INSERT INTO data_relations '
                                '(collection, record_id, field_name, related_collection, related_id, branch_id) '
                                'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                                (target_collection, related_id, target_field, collection, record_id, target_branch_id),
                            )

                summary['recordsUpdated'] += 1
            else:
                # 目标记录不存在
                summary['recordsNotFound'] += 1

        # 不再需要全量替换 relations，因为已经在上面按决策处理了
        # 仅清理未处理的孤立关联（删除不存在记录的关联）
        cur.execute(
            'DELETE FROM data_relations WHERE collection = %s AND branch_id = %s '
            'AND record_id NOT IN (SELECT id FROM dynamic_data WHERE collection = %s AND branch_id = %s)',
            (collection, target_branch_id, collection, target_branch_id),
        )

        # 6. 更新 data_hash（计算新状态的哈希）
        cur.execute(
            'SELECT id, data, created_at FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection, target_branch_id),
        )
        new_data_rows = cur.fetchall()
        cur.execute(
            'SELECT collection, record_id, field_name, related_collection, related_id '
            'FROM data_relations WHERE collection = %s AND branch_id = %s',
            (collection, target_branch_id),
        )
        new_rel_rows = cur.fetchall()

        new_records = [{'id': r[0], 'data': r[1], 'created_at': r[2]} for r in new_data_rows]
        new_relations = [list(r) for r in new_rel_rows]
        new_hash = _compute_data_hash(new_records, new_relations)

        # 更新版本状态
        cur.execute(
            'UPDATE collection_versions SET status = %s, merged_at = %s, merged_by = %s, data_hash = %s '
            'WHERE id = %s',
            ('merged', now, merged_by, new_hash, source_version_id),
        )

        merged_count = summary['recordsCreated'] + summary['recordsDeleted'] + summary['recordsUpdated']

        return {
            'success': True,
            'message': f'部分合并完成，共处理 {merged_count} 条记录',
            'merged_count': merged_count,
            'summary': summary,
        }


def restore_from_version(version_id, restored_by, user_id=None):
    """
    从版本恢复数据（完全覆盖当前数据）

    Parameters
    ----------
    version_id : str
        版本 ID
    restored_by : str
        恢复者
    user_id : str | None
        用户 ID，用于获取目标分支

    Returns
    -------
    dict
        {success, recordsCount, relationsCount}
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 获取版本信息
        cur.execute(
            'SELECT collection, records_count, relations_count FROM collection_versions WHERE id = %s',
            (version_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')
        collection, records_count, relations_count = row

        # 获取目标分支
        target_branch_id = get_user_current_branch(user_id, collection) if user_id else MAIN_BRANCH_ID

        # 加载版本数据
        version_records, version_rels = load_version_data(version_id)

        # 清空目标分支的当前数据
        cur.execute(
            'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection, target_branch_id)
        )

        # 插入快照数据（带目标分支 ID）
        for rec in version_records:
            data = {k: v for k, v in rec.items() if k != 'id'}
            cur.execute(
                'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
                (rec['id'], collection, psycopg2.extras.Json(data), target_branch_id),
            )

        # 恢复关联数据（带目标分支 ID）
        cur.execute(
            'SELECT collection, record_id, field_name, related_collection, related_id '
            'FROM version_relations WHERE version_id = %s',
            (version_id,),
        )
        _replace_collection_relations(cur, collection, target_branch_id, cur.fetchall())

    return {
        'success': True,
        'recordsCount': records_count,
        'relationsCount': relations_count,
    }


def switch_to_version(version_id, switched_by, user_id=None):
    """
    切换到指定分支（数据分支化模式）

    切换流程：
    1. 检查目标版本状态和锁定
    2. 如果分支没有数据，从快照初始化
    3. 设置用户当前工作分支
    4. 锁定目标分支

    Parameters
    ----------
    version_id : str
        目标版本 ID
    switched_by : str
        切换操作者用户名
    user_id : str | None
        用户 ID，用于设置当前分支

    Returns
    -------
    dict
        {success, branchId, recordsInBranch, initialized}
    """
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        cur = conn.cursor()

        # 1. 获取目标版本信息
        # 先检查 initialized_at 字段是否存在（向后兼容）
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'collection_versions' AND column_name = 'initialized_at')"
        )
        has_initialized_at = cur.fetchone()[0]

        if has_initialized_at:
            cur.execute(
                'SELECT collection, name, status, version_type, initialized_at FROM collection_versions WHERE id = %s',
                (version_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError('目标版本不存在')
            collection, target_name, status, version_type, initialized_at = row
        else:
            cur.execute(
                'SELECT collection, name, status, version_type FROM collection_versions WHERE id = %s',
                (version_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError('目标版本不存在')
            collection, target_name, status, version_type = row
            initialized_at = None  # 字段不存在时默认为 None

        # 只能切换到活跃的分支版本
        if status != 'active':
            raise ValueError(f'无法切换：版本状态为「{status}」')
        if version_type != 'branch':
            raise ValueError('只能切换到分支类型版本，快照不支持切换')

        # 2. 获取版本涉及的所有 Collection（提前获取，用于后续检查和初始化）
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s',
            (version_id,)
        )
        affected_collections = [row[0] for row in cur.fetchall()]

        # Fallback: if no tracking data, use metadata collection
        if not affected_collections:
            affected_collections = [collection]

        # 3. 检查是否已初始化（防止并发初始化）
        initialized = False
        existing_count = 0

        if initialized_at is not None:
            # 分支已初始化（initialized_at 有值），直接计算数据量
            cur.execute(
                'SELECT SUM(cnt) FROM (SELECT COUNT(*) as cnt FROM dynamic_data WHERE branch_id = %s AND collection = ANY(%s)) sub',
                (version_id, affected_collections)
            )
            result = cur.fetchone()
            existing_count = result[0] if result and result[0] else 0
            initialized = False  # 数据已存在，不是本次切换时初始化的
        else:
            # initialized_at 为 None（字段不存在或值为 NULL）
            # 需要初始化 - 使用 PostgreSQL advisory lock 防止并发
            import hashlib
            lock_key = int(hashlib.md5(version_id.encode()).hexdigest()[:8], 16)
            cur.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key,))

            # 锁定后再次检查是否已有数据（另一个事务可能已初始化）
            cur.execute(
                'SELECT SUM(cnt) FROM (SELECT COUNT(*) as cnt FROM dynamic_data WHERE branch_id = %s AND collection = ANY(%s)) sub',
                (version_id, affected_collections)
            )
            result = cur.fetchone()
            pre_existing_count = result[0] if result and result[0] else 0

            if pre_existing_count > 0:
                # 已经有数据，跳过初始化
                existing_count = pre_existing_count
                initialized = False
            else:
                # 执行初始化：调用辅助函数
                total_inserted = initialize_branch_from_snapshot(
                    version_id=version_id,
                    affected_collections=affected_collections,
                    cur=cur
                )

                # 标记初始化完成（仅在 initialized_at 字段存在时更新）
                if has_initialized_at:
                    cur.execute(
                        'UPDATE collection_versions SET initialized_at = %s WHERE id = %s',
                        (now, version_id)
                    )

                existing_count = total_inserted
                initialized = True

        # 5. 批量更新所有 Collection 的用户当前分支（原子操作）
        if user_id:
            now = datetime.now(timezone.utc)
            for coll in affected_collections:
                record_id = f'ucb-{user_id}-{coll}'
                # 使用 UPSERT 在同一事务中原子更新
                cur.execute(
                    'INSERT INTO user_current_branch (id, user_id, username, collection, branch_id, updated_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s) '
                    'ON CONFLICT (user_id, collection) DO UPDATE SET branch_id = %s, updated_at = %s',
                    (record_id, user_id, switched_by, coll, version_id, now, version_id, now),
                )

    return {
        'success': True,
        'branchId': version_id,
        'branchName': target_name,
        'recordsInBranch': existing_count,
        'initialized': initialized,
        'affectedCollections': affected_collections,
    }


def switch_to_main_branch(collection, switched_by, user_id=None):
    """
    切换到主分支

    Parameters
    ----------
    collection : str
        集合名称
    switched_by : str
        切换操作者用户名
    user_id : str | None
        用户 ID

    Returns
    -------
    dict
        {success}
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 获取主分支数据数量
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection, MAIN_BRANCH_ID),
        )
        main_count = cur.fetchone()[0]

        # 获取当前用户在同一分支的所有 Collection
        current_branch = MAIN_BRANCH_ID
        if user_id:
            # 直接查询用户当前分支（避免调用打开新连接的辅助函数）
            cur.execute(
                'SELECT branch_id FROM user_current_branch WHERE user_id = %s AND collection = %s',
                (user_id, collection),
            )
            row = cur.fetchone()
            current_branch = row[0] if row else MAIN_BRANCH_ID

            if current_branch != MAIN_BRANCH_ID:
                # 查询所有在同一分支的 Collection
                cur.execute(
                    'SELECT collection FROM user_current_branch '
                    'WHERE user_id = %s AND branch_id = %s',
                    (user_id, current_branch)
                )
                affected_collections = [row[0] for row in cur.fetchall()]
            else:
                # 已在主分支，只处理当前 Collection
                affected_collections = [collection]
        else:
            # 无用户 ID，只处理当前 Collection
            affected_collections = [collection]

        # 批量切换所有 Collection 到主分支（原子操作）
        if user_id and current_branch != MAIN_BRANCH_ID:
            # 使用单个 UPDATE 语句原子更新所有匹配的行
            now = datetime.now(timezone.utc)
            cur.execute(
                'UPDATE user_current_branch '
                'SET branch_id = %s, updated_at = %s '
                'WHERE user_id = %s AND branch_id = %s',
                (MAIN_BRANCH_ID, now, user_id, current_branch)
            )

    return {
        'success': True,
        'branchId': MAIN_BRANCH_ID,
        'branchName': '主分支',
        'recordsInBranch': main_count,
        'initialized': False,
        'affectedCollections': affected_collections,
    }


def get_current_branch(collection):
    """
    获取集合的当前工作分支

    通过检查 description 中是否包含 '[当前工作分支]' 标记来判断

    Parameters
    ----------
    collection : str
        集合名称

    Returns
    -------
    dict | None
        当前分支信息，如果没有则返回 None
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, description, version_type FROM collection_versions "
            "WHERE collection = %s AND status = 'active' AND version_type = 'branch' "
            "AND description LIKE %s",
            (collection, '%[当前工作分支]%'),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            'id': row[0],
            'name': row[1],
            'description': row[2],
            'versionType': row[3],
        }


