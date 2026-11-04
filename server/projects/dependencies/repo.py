"""
跨项目依赖管理核心逻辑

职责：
- 依赖声明管理（创建、更新、解除）
- 关联关系扫描
- 依赖合法性校验
- 分支删除保护检查

依赖类型：
- track-main: 跟随主干，自动接收target main分支更新
- read-write: 配套分支，需要联合合并
- read-only: 精确钉住，引用特定历史版本
"""

import uuid
from datetime import datetime, timezone
from db import get_db

from .scan import save_dependency_relations, scan_dependency_relations


# 常量定义
RELATION_TYPE_TRACK_MAIN = 'track-main'
RELATION_TYPE_READ_WRITE = 'read-write'
RELATION_TYPE_READ_ONLY = 'read-only'



# ==================== 依赖声明管理 ====================

def create_project_dependency(
    source_project: str,
    source_branch: str,
    target_project: str,
    target_branch: str,
    relation_type: str,
    pinned_version: str | None = None,
    declared_by: str | None = None
) -> dict:
    """
    创建项目依赖声明

    Parameters
    ----------
    source_project : str
        声明方项目菜单ID (menus.id where menu_type='project')
    source_branch : str
        声明方分支ID ('main' 或 project_versions.id)
    target_project : str
        被依赖方项目菜单ID
    target_branch : str
        被依赖方分支ID
    relation_type : str
        依赖模式: 'track-main' | 'read-write' | 'read-only'
    pinned_version : str | None
        精确钉住的版本ID (仅read-only模式)
    declared_by : str | None
        声明者用户名

    Returns
    -------
    dict
        创建的依赖声明信息
    """
    if relation_type not in [RELATION_TYPE_TRACK_MAIN, RELATION_TYPE_READ_WRITE, RELATION_TYPE_READ_ONLY]:
        raise ValueError(f'无效的依赖类型: {relation_type}')

    # 检查源项目和目标项目是否都是有效的项目菜单
    with get_db() as conn:
        cur = conn.cursor()

        # 验证源项目
        cur.execute(
            'SELECT id, name FROM menus WHERE id = %s AND menu_type = %s',
            (source_project, 'project')
        )
        source_row = cur.fetchone()
        if not source_row:
            raise ValueError(f'源项目不存在或不是项目类型: {source_project}')
        source_project_name = source_row[1]

        # 验证目标项目
        cur.execute(
            'SELECT id, name FROM menus WHERE id = %s AND menu_type = %s',
            (target_project, 'project')
        )
        target_row = cur.fetchone()
        if not target_row:
            raise ValueError(f'目标项目不存在或不是项目类型: {target_project}')
        target_project_name = target_row[1]

        # 检查是否已存在相同的依赖声明
        cur.execute(
            'SELECT id FROM project_dependencies WHERE source_project = %s AND source_branch = %s AND target_project = %s',
            (source_project, source_branch, target_project)
        )
        if cur.fetchone():
            raise ValueError(f'已存在相同的依赖声明')

        # 检查循环依赖
        from .checks import check_circular_dependency
        if check_circular_dependency(source_project, target_project):
            raise ValueError(f'存在循环依赖，无法创建')

        # 创建依赖声明
        dep_id = f'dep-{uuid.uuid4().hex[:12]}'
        now = datetime.now(timezone.utc)

        cur.execute(
            '''INSERT INTO project_dependencies
               (id, source_project, source_branch, target_project, target_branch, relation_type, pinned_version, declared_by, declared_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, source_project, source_branch, target_project, target_branch, relation_type, pinned_version, is_validated, declared_by, declared_at''',
            (dep_id, source_project, source_branch, target_project, target_branch, relation_type, pinned_version, declared_by, now, now)
        )
        row = cur.fetchone()

        conn.commit()

        # 扫描并保存关联关系
        relations = scan_dependency_relations(source_project, target_project)
        saved_relations = save_dependency_relations(dep_id, relations)

        return {
            'id': row[0],
            'sourceProject': row[1],
            'sourceBranch': row[2],
            'targetProject': row[3],
            'targetBranch': row[4],
            'relationType': row[5],
            'pinnedVersion': row[6],
            'isValidated': row[7],
            'declaredBy': row[8],
            'declaredAt': row[9].isoformat() if row[9] else None,
            'sourceProjectName': source_project_name,
            'targetProjectName': target_project_name,
            'relations': saved_relations,
        }


def get_project_dependencies(project_menu_id: str, branch_id: str | None = None) -> list[dict]:
    """
    获取项目的依赖声明列表

    Parameters
    ----------
    project_menu_id : str
        项目菜单ID
    branch_id : str | None
        分支ID，不指定则返回所有分支的依赖

    Returns
    -------
    list[dict]
        依赖声明列表
    """
    with get_db() as conn:
        cur = conn.cursor()

        if branch_id:
            cur.execute(
                '''SELECT d.id, d.source_project, d.source_branch, d.target_project, d.target_branch,
                          d.relation_type, d.pinned_version, d.is_validated, d.validation_error,
                          d.declared_by, d.declared_at, d.updated_at,
                          sm.name as source_project_name, tm.name as target_project_name
                   FROM project_dependencies d
                   LEFT JOIN menus sm ON d.source_project = sm.id
                   LEFT JOIN menus tm ON d.target_project = tm.id
                   WHERE d.source_project = %s AND d.source_branch = %s
                   ORDER BY d.declared_at DESC''',
                (project_menu_id, branch_id)
            )
        else:
            cur.execute(
                '''SELECT d.id, d.source_project, d.source_branch, d.target_project, d.target_branch,
                          d.relation_type, d.pinned_version, d.is_validated, d.validation_error,
                          d.declared_by, d.declared_at, d.updated_at,
                          sm.name as source_project_name, tm.name as target_project_name
                   FROM project_dependencies d
                   LEFT JOIN menus sm ON d.source_project = sm.id
                   LEFT JOIN menus tm ON d.target_project = tm.id
                   WHERE d.source_project = %s
                   ORDER BY d.declared_at DESC''',
                (project_menu_id,)
            )

        rows = cur.fetchall()
        deps = []

        for row in rows:
            dep = {
                'id': row[0],
                'sourceProject': row[1],
                'sourceBranch': row[2],
                'targetProject': row[3],
                'targetBranch': row[4],
                'relationType': row[5],
                'pinnedVersion': row[6],
                'isValidated': row[7],
                'validationError': row[8],
                'declaredBy': row[9],
                'declaredAt': row[10].isoformat() if row[10] else None,
                'updatedAt': row[11].isoformat() if row[11] else None,
                'sourceProjectName': row[12],
                'targetProjectName': row[13],
            }
            deps.append(dep)

        return deps


def get_dependent_projects(project_menu_id: str, branch_id: str | None = None) -> list[dict]:
    """
    获取依赖于此项目的项目列表（反向查询）

    Parameters
    ----------
    project_menu_id : str
        被依赖方项目菜单ID
    branch_id : str | None
        分支ID

    Returns
    -------
    list[dict]
        依赖方项目列表
    """
    with get_db() as conn:
        cur = conn.cursor()

        if branch_id:
            cur.execute(
                '''SELECT d.id, d.source_project, d.source_branch, d.target_project, d.target_branch,
                          d.relation_type, d.pinned_version, d.is_validated, d.validation_error,
                          d.declared_by, d.declared_at, d.updated_at,
                          sm.name as source_project_name, tm.name as target_project_name
                   FROM project_dependencies d
                   LEFT JOIN menus sm ON d.source_project = sm.id
                   LEFT JOIN menus tm ON d.target_project = tm.id
                   WHERE d.target_project = %s AND d.target_branch = %s
                   ORDER BY d.declared_at DESC''',
                (project_menu_id, branch_id)
            )
        else:
            cur.execute(
                '''SELECT d.id, d.source_project, d.source_branch, d.target_project, d.target_branch,
                          d.relation_type, d.pinned_version, d.is_validated, d.validation_error,
                          d.declared_by, d.declared_at, d.updated_at,
                          sm.name as source_project_name, tm.name as target_project_name
                   FROM project_dependencies d
                   LEFT JOIN menus sm ON d.source_project = sm.id
                   LEFT JOIN menus tm ON d.target_project = tm.id
                   WHERE d.target_project = %s
                   ORDER BY d.declared_at DESC''',
                (project_menu_id,)
            )

        rows = cur.fetchall()
        deps = []

        for row in rows:
            dep = {
                'id': row[0],
                'sourceProject': row[1],
                'sourceBranch': row[2],
                'targetProject': row[3],
                'targetBranch': row[4],
                'relationType': row[5],
                'pinnedVersion': row[6],
                'isValidated': row[7],
                'validationError': row[8],
                'declaredBy': row[9],
                'declaredAt': row[10].isoformat() if row[10] else None,
                'updatedAt': row[11].isoformat() if row[11] else None,
                'sourceProjectName': row[12],
                'targetProjectName': row[13],
            }
            deps.append(dep)

        return deps


def get_dependency_by_id(dependency_id: str) -> dict | None:
    """
    根据ID获取依赖声明详情
    """
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            '''SELECT d.id, d.source_project, d.source_branch, d.target_project, d.target_branch,
                      d.relation_type, d.pinned_version, d.is_validated, d.validation_error,
                      d.declared_by, d.declared_at, d.updated_at,
                      sm.name as source_project_name, tm.name as target_project_name
               FROM project_dependencies d
               LEFT JOIN menus sm ON d.source_project = sm.id
               LEFT JOIN menus tm ON d.target_project = tm.id
               WHERE d.id = %s''',
            (dependency_id,)
        )
        row = cur.fetchone()

        if not row:
            return None

        return {
            'id': row[0],
            'sourceProject': row[1],
            'sourceBranch': row[2],
            'targetProject': row[3],
            'targetBranch': row[4],
            'relationType': row[5],
            'pinnedVersion': row[6],
            'isValidated': row[7],
            'validationError': row[8],
            'declaredBy': row[9],
            'declaredAt': row[10].isoformat() if row[10] else None,
            'updatedAt': row[11].isoformat() if row[11] else None,
            'sourceProjectName': row[12],
            'targetProjectName': row[13],
        }


def update_project_dependency(dependency_id: str, **kwargs) -> dict:
    """
    更新依赖声明

    Parameters
    ----------
    dependency_id : str
        依赖声明ID
    kwargs : dict
        可更新字段: target_branch, relation_type, pinned_version

    Returns
    -------
    dict
        更新后的依赖声明信息
    """
    allowed_fields = ['target_branch', 'relation_type', 'pinned_version']
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

    if not updates:
        raise ValueError('没有可更新的字段')

    if 'relation_type' in updates and updates['relation_type'] not in [RELATION_TYPE_TRACK_MAIN, RELATION_TYPE_READ_WRITE, RELATION_TYPE_READ_ONLY]:
        raise ValueError(f'无效的依赖类型: {updates["relation_type"]}')

    with get_db() as conn:
        cur = conn.cursor()

        # 构建更新SQL
        set_clause = ', '.join([f'{k} = %s' for k in updates.keys()])
        set_clause += ', updated_at = %s'
        values = list(updates.values()) + [datetime.now(timezone.utc)]

        cur.execute(
            f'UPDATE project_dependencies SET {set_clause} WHERE id = %s',
            values + [dependency_id]
        )

        if cur.rowcount == 0:
            raise ValueError(f'依赖声明不存在: {dependency_id}')

        conn.commit()

        return get_dependency_by_id(dependency_id)


def delete_project_dependency(dependency_id: str) -> bool:
    """
    解除依赖声明

    Parameters
    ----------
    dependency_id : str
        依赖声明ID

    Returns
    -------
    bool
        是否成功解除
    """
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute('DELETE FROM project_dependencies WHERE id = %s', (dependency_id,))

        if cur.rowcount == 0:
            raise ValueError(f'依赖声明不存在: {dependency_id}')

        conn.commit()

        return True



# ==================== 合法性校验 ====================

def check_branch_exists(project_menu_id: str, branch_id: str) -> bool:
    """
    检查分支是否存在

    Parameters
    ----------
    project_menu_id : str
        项目菜单ID
    branch_id : str
        分支ID ('main' 或 project_versions.id)

    Returns
    -------
    bool
        分支是否存在
    """
    with get_db() as conn:
        cur = conn.cursor()

        if branch_id == 'main':
            # main分支总是存在（项目存在即存在）
            cur.execute(
                'SELECT id FROM menus WHERE id = %s AND menu_type = %s',
                (project_menu_id, 'project')
            )
            return cur.fetchone() is not None
        else:
            # 检查project_versions表
            cur.execute(
                'SELECT id FROM project_versions WHERE id = %s AND project_menu_id = %s AND status = %s',
                (branch_id, project_menu_id, 'active')
            )
            return cur.fetchone() is not None



def update_dependency_after_merge(dependency_id: str, new_target_branch: str = 'main') -> None:
    """
    合并成功后更新依赖声明

    Parameters
    ----------
    dependency_id : str
        依赖声明ID
    new_target_branch : str
        新的目标分支（默认为 main）
    """
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            '''UPDATE project_dependencies
               SET target_branch = %s, updated_at = %s, is_validated = FALSE
               WHERE id = %s''',
            (new_target_branch, datetime.now(timezone.utc), dependency_id)
        )
        conn.commit()


def batch_update_dependencies_after_merge(project_menu_id: str, source_branch: str) -> int:
    """
    合并成功后批量更新依赖声明

    将所有 read-write 模式的依赖声明更新为 target_branch = 'main'

    Parameters
    ----------
    project_menu_id : str
        项目菜单ID
    source_branch : str
        源分支ID

    Returns
    -------
    int
        更新的依赖声明数量
    """
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            '''UPDATE project_dependencies
               SET target_branch = 'main', updated_at = %s, is_validated = FALSE
               WHERE source_project = %s AND source_branch = %s AND relation_type = %s AND target_branch != 'main' ''',
            (datetime.now(timezone.utc), project_menu_id, source_branch, RELATION_TYPE_READ_WRITE)
        )
        updated_count = cur.rowcount
        conn.commit()

        return updated_count


__all__ = [
    'RELATION_TYPE_TRACK_MAIN',
    'RELATION_TYPE_READ_WRITE',
    'RELATION_TYPE_READ_ONLY',
    'datetime',
    'timezone',
    'get_db',
    'create_project_dependency',
    'get_project_dependencies',
    'get_dependent_projects',
    'get_dependency_by_id',
    'update_project_dependency',
    'delete_project_dependency',
    'check_branch_exists',
    'update_dependency_after_merge',
    'batch_update_dependencies_after_merge',
]
