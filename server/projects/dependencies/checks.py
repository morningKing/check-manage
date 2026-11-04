from datetime import datetime, timezone
from db import get_db

from .repo import (RELATION_TYPE_READ_ONLY, RELATION_TYPE_READ_WRITE,
                   RELATION_TYPE_TRACK_MAIN, check_branch_exists,
                   get_dependency_by_id)
from .scan import get_dependency_relations


VALIDATION_STATUS_VALID = 'valid'
VALIDATION_STATUS_BROKEN = 'broken'
VALIDATION_STATUS_WARNING = 'warning'
VALIDATION_STATUS_UNKNOWN = 'unknown'



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


def validate_project_dependency(dependency_id: str, send_notification: bool = True) -> dict:
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
    send_notification : bool
        是否发送通知（默认True）

    Returns
    -------
    dict
        校验结果
    """
    dep = get_dependency_by_id(dependency_id)
    if not dep:
        raise ValueError(f'依赖声明不存在: {dependency_id}')

    # 记录之前的校验状态（用于判断是否需要发送通知）
    previous_is_valid = dep.get('isValidated', False)
    previous_error = dep.get('validationError')

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

    # 发送通知（仅在状态变化时）
    if send_notification:
        from common.notifier import (
            notify_dependency_broken,
            notify_dependency_warning,
            notify_dependency_resolved,
        )
        source_project_name = dep.get('sourceProjectName', '未知项目')
        target_project_name = dep.get('targetProjectName', '未知项目')

        # 从有效变为无效
        if previous_is_valid and not is_valid:
            notify_dependency_broken(
                dep['sourceProject'],
                source_project_name,
                target_project_name,
                validation_error or '未知原因'
            )
        # 无效变为有效
        elif not previous_is_valid and is_valid:
            notify_dependency_resolved(
                dep['sourceProject'],
                source_project_name,
                target_project_name
            )
        # 有效但有警告
        elif is_valid and warnings:
            notify_dependency_warning(
                dep['sourceProject'],
                source_project_name,
                target_project_name,
                '; '.join(warnings)
            )

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


# ==================== 联合合并编排 ====================

def check_merge_dependencies(project_menu_id: str, source_branch: str) -> dict:
    """
    检查合并前的依赖状态

    Parameters
    ----------
    project_menu_id : str
        源项目菜单ID
    source_branch : str
        要合并的源分支ID

    Returns
    -------
    dict
        {
            'canMerge': bool,
            'blockingDependencies': list[dict],
            'readyDependencies': list[dict],
            'trackMainDependencies': list[dict],
            'readOnlyDependencies': list[dict],
        }
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 查询该分支的所有依赖声明
        cur.execute(
            '''SELECT d.id, d.target_project, d.target_branch, d.relation_type, d.pinned_version,
                      tm.name as target_project_name
               FROM project_dependencies d
               LEFT JOIN menus tm ON d.target_project = tm.id
               WHERE d.source_project = %s AND d.source_branch = %s''',
            (project_menu_id, source_branch)
        )
        dependencies = cur.fetchall()

        blocking = []
        ready = []
        track_main = []
        read_only = []

        for row in dependencies:
            dep_id, target_project, target_branch, relation_type, pinned_version, target_project_name = row

            dep_info = {
                'id': dep_id,
                'targetProject': target_project,
                'targetBranch': target_branch,
                'relationType': relation_type,
                'pinnedVersion': pinned_version,
                'targetProjectName': target_project_name,
            }

            if relation_type == RELATION_TYPE_READ_ONLY:
                # read-only 模式：数据钉住特定版本，不影响合并
                read_only.append(dep_info)

            elif relation_type == RELATION_TYPE_TRACK_MAIN:
                # track-main 模式：目标分支是 main，检查是否有破坏性变更
                # 这里只记录，实际合并时再检查
                track_main.append(dep_info)

            elif relation_type == RELATION_TYPE_READ_WRITE:
                # read-write 模式：配套分支，需要检查目标分支是否已合并
                if target_branch == 'main':
                    # 目标已经是main，可以合并
                    ready.append(dep_info)
                else:
                    # 检查目标分支是否已合并到main
                    cur.execute(
                        '''SELECT status, merged_at FROM project_versions
                           WHERE id = %s AND project_menu_id = %s''',
                        (target_branch, target_project)
                    )
                    target_row = cur.fetchone()

                    if target_row:
                        target_status, merged_at = target_row
                        if target_status == 'merged' or merged_at:
                            # 目标分支已合并，可以合并
                            ready.append(dep_info)
                        else:
                            # 目标分支未合并，阻塞
                            dep_info['reason'] = '目标分支尚未合并到主分支'
                            blocking.append(dep_info)
                    else:
                        # 目标分支不存在
                        dep_info['reason'] = '目标分支不存在'
                        blocking.append(dep_info)

        can_merge = len(blocking) == 0

        return {
            'canMerge': can_merge,
            'blockingDependencies': blocking,
            'readyDependencies': ready,
            'trackMainDependencies': track_main,
            'readOnlyDependencies': read_only,
        }


def get_coordinated_merge_order(project_menu_id: str, source_branch: str) -> list[dict]:
    """
    获取联合合并的执行顺序

    通过依赖图分析，确定合并顺序：
    1. 先合并被依赖的项目（下游）
    2. 后合并依赖方项目（上游）

    Parameters
    ----------
    project_menu_id : str
        项目菜单ID
    source_branch : str
        源分支ID

    Returns
    -------
    list[dict]
        合并顺序列表，每项包含 project_menu_id, branch_id, version_name
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 获取版本信息
        cur.execute(
            'SELECT name FROM project_versions WHERE id = %s',
            (source_branch,)
        )
        version_row = cur.fetchone()
        if not version_row:
            raise ValueError(f'版本不存在: {source_branch}')
        version_name = version_row[0]

        # 构建合并顺序
        merge_order = []

        # 1. 先找出所有 read-write 依赖且目标分支未合并的项目
        cur.execute(
            '''SELECT d.target_project, d.target_branch, tm.name as target_project_name,
                      pv.name as target_branch_name
               FROM project_dependencies d
               LEFT JOIN menus tm ON d.target_project = tm.id
               LEFT JOIN project_versions pv ON d.target_branch = pv.id
               WHERE d.source_project = %s AND d.source_branch = %s AND d.relation_type = %s''',
            (project_menu_id, source_branch, RELATION_TYPE_READ_WRITE)
        )
        read_write_deps = cur.fetchall()

        for row in read_write_deps:
            target_project, target_branch, target_project_name, target_branch_name = row

            if target_branch != 'main':
                # 检查目标分支是否已合并
                cur.execute(
                    'SELECT status FROM project_versions WHERE id = %s AND project_menu_id = %s',
                    (target_branch, target_project)
                )
                status_row = cur.fetchone()

                if status_row and status_row[0] != 'merged':
                    # 目标分支未合并，需要先合并它
                    merge_order.append({
                        'projectMenuId': target_project,
                        'branchId': target_branch,
                        'projectName': target_project_name,
                        'branchName': target_branch_name or target_branch,
                        'isDependency': True,
                    })

        # 2. 最后是当前项目
        merge_order.append({
            'projectMenuId': project_menu_id,
            'branchId': source_branch,
            'projectName': None,  # 当前项目
            'branchName': version_name,
            'isDependency': False,
        })

        return merge_order



__all__ = [
    'VALIDATION_STATUS_VALID',
    'VALIDATION_STATUS_BROKEN',
    'VALIDATION_STATUS_WARNING',
    'VALIDATION_STATUS_UNKNOWN',
    'datetime',
    'timezone',
    'get_db',
    'check_data_reachability',
    'check_circular_dependency',
    'validate_project_dependency',
    'check_branch_delete_protection',
    'check_merge_dependencies',
    'get_coordinated_merge_order',
]
