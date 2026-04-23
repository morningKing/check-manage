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
import psycopg2.extras
from utils.page_config_relations import extract_target_collection
from utils.project_version import get_project_collections


# 常量定义
RELATION_TYPE_TRACK_MAIN = 'track-main'
RELATION_TYPE_READ_WRITE = 'read-write'
RELATION_TYPE_READ_ONLY = 'read-only'

VALIDATION_STATUS_VALID = 'valid'
VALIDATION_STATUS_BROKEN = 'broken'
VALIDATION_STATUS_WARNING = 'warning'
VALIDATION_STATUS_UNKNOWN = 'unknown'


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


# ==================== 关联关系扫描 ====================

def scan_dependency_relations(source_project: str, target_project: str) -> list[dict]:
    """
    扫描源项目与目标项目之间的关联关系

    复用 page_config_relations.py 的 extract_target_collection 逻辑

    Parameters
    ----------
    source_project : str
        源项目菜单ID
    target_project : str
        目标项目菜单ID

    Returns
    -------
    list[dict]
        关联关系列表
    """
    # 获取源项目和目标项目的所有collection
    source_collections = get_project_collections(source_project)
    target_collections = get_project_collections(target_project)
    target_collection_names = {c['collection'] for c in target_collections}

    relations = []
    with get_db() as conn:
        cur = conn.cursor()

        for coll_info in source_collections:
            collection = coll_info['collection']
            page_id = coll_info['pageId']

            # 获取page_config的fields
            cur.execute(
                'SELECT fields FROM page_configs WHERE id = %s',
                (page_id,)
            )
            row = cur.fetchone()
            if not row or not row[0]:
                continue

            fields = row[0]
            for field in fields:
                target_collection = extract_target_collection(field)

                if target_collection and target_collection in target_collection_names:
                    relations.append({
                        'source_collection': collection,
                        'source_field': field.get('fieldName'),
                        'target_collection': target_collection,
                        'control_type': field.get('controlType'),
                    })

    return relations


def save_dependency_relations(dependency_id: str, relations: list[dict]) -> list[dict]:
    """
    保存依赖涉及的关联关系

    Parameters
    ----------
    dependency_id : str
        依赖声明ID
    relations : list[dict]
        关联关系列表

    Returns
    -------
    list[dict]
        保存的关联关系记录
    """
    with get_db() as conn:
        cur = conn.cursor()

        saved_relations = []
        for rel in relations:
            rel_id = f'pdr-{uuid.uuid4().hex[:12]}'

            cur.execute(
                '''INSERT INTO project_dependency_relations
                   (id, dependency_id, source_collection, source_field, target_collection, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, dependency_id, source_collection, source_field, target_collection, validation_status''',
                (rel_id, dependency_id, rel['source_collection'], rel['source_field'], rel['target_collection'], datetime.now(timezone.utc))
            )
            row = cur.fetchone()

            saved_relations.append({
                'id': row[0],
                'dependencyId': row[1],
                'sourceCollection': row[2],
                'sourceField': row[3],
                'targetCollection': row[4],
                'validationStatus': row[5],
                'controlType': rel.get('control_type'),
            })

        conn.commit()

        return saved_relations


def get_dependency_relations(dependency_id: str) -> list[dict]:
    """
    获取依赖声明涉及的关联关系
    """
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            '''SELECT id, dependency_id, source_collection, source_field, target_collection,
                      estimated_records, validation_status, validation_detail, validated_at
               FROM project_dependency_relations
               WHERE dependency_id = %s
               ORDER BY source_collection, source_field''',
            (dependency_id,)
        )
        rows = cur.fetchall()

        relations = []
        for row in rows:
            relations.append({
                'id': row[0],
                'dependencyId': row[1],
                'sourceCollection': row[2],
                'sourceField': row[3],
                'targetCollection': row[4],
                'estimatedRecords': row[5],
                'validationStatus': row[6],
                'validationDetail': row[7],
                'validatedAt': row[8].isoformat() if row[8] else None,
            })

        return relations


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


def check_data_reachability(
    source_collection: str,
    source_branch: str,
    target_collection: str,
    target_branch: str
) -> dict:
    """
    检查数据可达性（外键完整性）

    Parameters
    ----------
    source_collection : str
        源 collection
    source_branch : str
        源分支ID
    target_collection : str
        目标 collection
    target_branch : str
        目标分支ID

    Returns
    -------
    dict
        可达性检查结果
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 查询源collection的所有关联关系（指向目标collection）
        cur.execute(
            '''SELECT DISTINCT record_id, field_name, related_id
               FROM data_relations
               WHERE collection = %s AND branch_id = %s AND related_collection = %s''',
            (source_collection, source_branch, target_collection)
        )
        relations = cur.fetchall()

        reachable_count = 0
        broken_records = []

        for record_id, field_name, related_id in relations:
            # 检查目标记录是否存在
            cur.execute(
                'SELECT id FROM dynamic_data WHERE id = %s AND collection = %s AND branch_id = %s',
                (related_id, target_collection, target_branch)
            )
            if cur.fetchone():
                reachable_count += 1
            else:
                broken_records.append({
                    'recordId': record_id,
                    'fieldName': field_name,
                    'relatedId': related_id,
                })

        return {
            'reachableCount': reachable_count,
            'brokenCount': len(broken_records),
            'brokenRecords': broken_records[:10],  # 仅返回前10条
        }


def check_circular_dependency(source_project: str, target_project: str) -> bool:
    """
    检查是否存在循环依赖

    Parameters
    ----------
    source_project : str
        源项目菜单ID
    target_project : str
        目标项目菜单ID

    Returns
    -------
    bool
        是否存在循环依赖
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 检查target_project是否已经依赖source_project
        cur.execute(
            'SELECT id FROM project_dependencies WHERE source_project = %s AND target_project = %s',
            (target_project, source_project)
        )
        if cur.fetchone():
            return True

        # 递归检查（最多3层）
        visited = set()
        queue = [target_project]

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            # 找到current依赖的所有项目
            cur.execute(
                'SELECT target_project FROM project_dependencies WHERE source_project = %s',
                (current,)
            )
            for row in cur.fetchall():
                next_project = row[0]
                if next_project == source_project:
                    return True
                if next_project not in visited:
                    queue.append(next_project)

        return False


def validate_project_dependency(dependency_id: str) -> dict:
    """
    校验依赖合法性

    校验维度：
    1. 分支存在性：target 分支是否存在
    2. 数据可达性：外键指向的记录在 target 分支是否存在
    3. 环检测：是否存在循环依赖

    Parameters
    ----------
    dependency_id : str
        依赖声明ID

    Returns
    -------
    dict
        校验结果
    """
    dep = get_dependency_by_id(dependency_id)
    if not dep:
        raise ValueError(f'依赖声明不存在: {dependency_id}')

    errors = []
    warnings = []
    relation_validations = []

    # 1. 检查目标分支存在性
    target_branch_exists = check_branch_exists(dep['targetProject'], dep['targetBranch'])
    if not target_branch_exists:
        errors.append(f'目标分支 {dep["targetBranch"]} 不存在')

    # 2. 检查循环依赖
    if check_circular_dependency(dep['sourceProject'], dep['targetProject']):
        errors.append('存在循环依赖')

    # 3. 检查数据可达性
    relations = get_dependency_relations(dependency_id)
    if relations and target_branch_exists:
        for rel in relations:
            reachability = check_data_reachability(
                rel['sourceCollection'],
                dep['sourceBranch'],
                rel['targetCollection'],
                dep['targetBranch']
            )

            status = VALIDATION_STATUS_VALID
            detail = None

            if reachability['brokenCount'] > 0:
                status = VALIDATION_STATUS_BROKEN
                detail = f'{reachability["brokenCount"]} 条外键断裂'
                warnings.append(f'{rel["sourceCollection"]}.{rel["sourceField"]}: {detail}')

            relation_validations.append({
                'relationId': rel['id'],
                'sourceCollection': rel['sourceCollection'],
                'sourceField': rel['sourceField'],
                'status': status,
                'detail': detail,
            })

            # 更新关联关系校验状态
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    '''UPDATE project_dependency_relations
                       SET validation_status = %s, validation_detail = %s, validated_at = %s, estimated_records = %s
                       WHERE id = %s''',
                    (status, detail, datetime.now(timezone.utc), reachability['reachableCount'] + reachability['brokenCount'], rel['id'])
                )
                conn.commit()

    # 更新依赖声明校验状态
    is_valid = len(errors) == 0
    validation_error = '; '.join(errors) if errors else None

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE project_dependencies SET is_validated = %s, validation_error = %s, updated_at = %s WHERE id = %s',
            (is_valid, validation_error, datetime.now(timezone.utc), dependency_id)
        )
        conn.commit()

    return {
        'isValid': is_valid,
        'errors': errors,
        'warnings': warnings,
        'relationValidations': relation_validations,
    }


# ==================== 分支删除保护 ====================

def check_branch_delete_protection(project_menu_id: str, branch_id: str) -> dict:
    """
    检查分支删除保护

    Parameters
    ----------
    project_menu_id : str
        项目菜单ID
    branch_id : str
        要删除的分支ID

    Returns
    -------
    dict
        检查结果
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 查找所有依赖此分支的项目
        cur.execute(
            '''SELECT d.id, d.source_project, d.source_branch, d.target_branch,
                      sm.name as source_project_name
               FROM project_dependencies d
               LEFT JOIN menus sm ON d.source_project = sm.id
               WHERE d.target_project = %s AND d.target_branch = %s''',
            (project_menu_id, branch_id)
        )
        dependents = cur.fetchall()

        blocking_deps = []
        dependent_projects = []

        for row in dependents:
            dep = {
                'id': row[0],
                'sourceProject': row[1],
                'sourceBranch': row[2],
                'targetBranch': row[3],
                'sourceProjectName': row[4],
            }
            blocking_deps.append(dep)

            # 收集依赖方项目信息
            dependent_projects.append({
                'projectId': row[1],
                'projectName': row[4],
                'branchId': row[2],
            })

        can_delete = len(blocking_deps) == 0

        return {
            'canDelete': can_delete,
            'dependentProjects': dependent_projects,
            'blockingDependencies': blocking_deps,
        }