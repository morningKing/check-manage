import uuid
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
            'parent_version, records_count, is_protected, is_locked, locked_at, locked_by '
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
                'isLocked': row[10],
                'lockedAt': row[11].isoformat() if row[11] else None,
                'lockedBy': row[12],
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
        from projects.version import set_user_current_branch

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
            'parent_version, records_count, is_protected, is_locked, locked_at, locked_by '
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
            'isLocked': row[11],
            'lockedAt': row[12].isoformat() if row[12] else None,
            'lockedBy': row[13],
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

        # 获取版本信息（包括项目ID）
        cur.execute('SELECT is_protected, project_menu_id FROM project_versions WHERE id = %s', (version_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')
        is_protected, project_menu_id = row
        if is_protected:
            raise ValueError('受保护的版本不能删除')

        # 检查跨项目依赖保护
        from projects.dependencies import check_branch_delete_protection
        protection = check_branch_delete_protection(project_menu_id, version_id)
        if not protection['canDelete']:
            dependent_names = [p['projectName'] for p in protection['dependentProjects']]
            raise ValueError(f'无法删除：以下项目依赖此分支: {", ".join(dependent_names)}')

        # 检查是否有子分支
        cur.execute('SELECT COUNT(*) FROM project_versions WHERE parent_version = %s', (version_id,))
        child_count = cur.fetchone()[0]
        if child_count > 0:
            raise ValueError('存在子分支，无法删除')

        # 删除（级联删除快照数据）
        cur.execute('DELETE FROM project_versions WHERE id = %s', (version_id,))

        return True



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
    from projects.version import set_user_current_branch

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



__all__ = [
    'MAIN_BRANCH_ID',
    'datetime',
    'timezone',
    'get_db',
    'get_project_collections',
    'get_user_project_branch',
    'set_user_project_branch',
    'create_project_version',
    'list_project_versions',
    'switch_project_branch',
    'initialize_project_branch_data',
    'get_project_version_detail',
    'delete_project_version',
    'switch_to_main_project_branch',
]
