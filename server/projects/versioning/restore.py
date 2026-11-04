from datetime import datetime, timezone
from db import get_db
import psycopg2.extras

from .repo import MAIN_BRANCH_ID, get_project_collections, get_user_project_branch


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

        # 闭合 autoSequence 计数器不变式：快照记录可能携带超过当前分支计数器的编号，
        # 重播种当前分支，避免还原后下一次 create_item 重号。
        from data.sequences import reseed_sequences
        restored_collections = [c['collection'] for c in collections]
        reseed_sequences(cur, collections=restored_collections, branch_id=current_branch)

    return {
        'success': True,
        'recordsCount': total_records,
        'relationsCount': total_relations,
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


# ==================== 分支锁定功能 ====================

def lock_project_version(version_id, locked_by, reason=None):
    """
    锁定项目分支

    Parameters
    ----------
    version_id : str
        版本 ID
    locked_by : str
        锁定者用户名
    reason : str, optional
        锁定原因

    Returns
    -------
    dict
        {success, is_locked, locked_at, locked_by}
    """
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        cur = conn.cursor()

        # 检查版本是否存在且为分支类型
        cur.execute(
            'SELECT version_type, status, is_locked FROM project_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')

        version_type, status, is_locked = row

        if version_type != 'branch':
            raise ValueError('只有分支类型可以锁定，快照不可锁定')

        if status != 'active':
            raise ValueError('只有活跃状态的分支可以锁定')

        if is_locked:
            raise ValueError('该分支已被锁定')

        # 执行锁定
        cur.execute(
            'UPDATE project_versions SET is_locked = TRUE, locked_at = %s, locked_by = %s WHERE id = %s',
            (now, locked_by, version_id)
        )

    return {
        'success': True,
        'isLocked': True,
        'lockedAt': now.isoformat(),
        'lockedBy': locked_by,
    }


def unlock_project_version(version_id):
    """
    解锁项目分支

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    dict
        {success, is_locked}
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 检查版本是否存在
        cur.execute(
            'SELECT is_locked FROM project_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')

        is_locked = row[0]
        if not is_locked:
            raise ValueError('该分支未被锁定')

        # 执行解锁
        cur.execute(
            'UPDATE project_versions SET is_locked = FALSE, locked_at = NULL, locked_by = NULL WHERE id = %s',
            (version_id,)
        )

    return {
        'success': True,
        'isLocked': False,
    }



def lock_main_branch(project_menu_id, locked_by, reason=None):
    """
    锁定项目的 main 分支

    Parameters
    ----------
    project_menu_id : str
        项目菜单 ID
    locked_by : str
        锁定者用户名
    reason : str, optional
        锁定原因

    Returns
    -------
    dict
        {success, is_locked, locked_at, locked_by}
    """
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        cur = conn.cursor()

        # 检查是否为项目菜单
        cur.execute(
            'SELECT menu_type, is_main_locked FROM menus WHERE id = %s',
            (project_menu_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('项目菜单不存在')

        menu_type, is_locked = row
        if menu_type != 'project':
            raise ValueError('只能锁定项目类型的 main 分支')

        if is_locked:
            raise ValueError('main 分支已被锁定')

        # 执行锁定
        cur.execute(
            'UPDATE menus SET is_main_locked = TRUE, main_locked_at = %s, main_locked_by = %s WHERE id = %s',
            (now, locked_by, project_menu_id)
        )

    return {
        'success': True,
        'isLocked': True,
        'lockedAt': now.isoformat(),
        'lockedBy': locked_by,
        'branchId': MAIN_BRANCH_ID,
    }


def unlock_main_branch(project_menu_id):
    """
    解锁项目的 main 分支

    Parameters
    ----------
    project_menu_id : str
        项目菜单 ID

    Returns
    -------
    dict
        {success, is_locked}
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 检查项目菜单
        cur.execute(
            'SELECT menu_type, is_main_locked FROM menus WHERE id = %s',
            (project_menu_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('项目菜单不存在')

        menu_type, is_locked = row
        if menu_type != 'project':
            raise ValueError('只能解锁项目类型的 main 分支')

        if not is_locked:
            raise ValueError('main 分支未被锁定')

        # 执行解锁
        cur.execute(
            'UPDATE menus SET is_main_locked = FALSE, main_locked_at = NULL, main_locked_by = NULL WHERE id = %s',
            (project_menu_id,)
        )

    return {
        'success': True,
        'isLocked': False,
        'branchId': MAIN_BRANCH_ID,
    }


__all__ = [
    'datetime',
    'timezone',
    'get_db',
    'restore_from_project_version',
    'get_project_version_delete_impact',
    'lock_project_version',
    'unlock_project_version',
    'lock_main_branch',
    'unlock_main_branch',
]
