from flask import Blueprint, request, jsonify, g
from auth import login_required, write_required, require_permission
from utils.project_version import (
    get_project_collections,
    get_user_project_branch,
    set_user_project_branch,
    create_project_version,
    list_project_versions,
    switch_project_branch,
    get_project_version_detail,
    delete_project_version,
    compute_project_version_diff,
    merge_project_version,
    merge_project_version_detailed,
    restore_from_project_version,
    switch_to_main_project_branch,
    get_project_version_delete_impact,
    lock_project_version,
    unlock_project_version,
    lock_main_branch,
    unlock_main_branch,
)

project_versions_bp = Blueprint('project_versions', __name__)


# ==================== 静态路由（放在动态路由前面）====================

@project_versions_bp.route('/project-versions/all-branches', methods=['GET'])
@login_required
def list_all_branches():
    """获取所有项目的分支列表（用于数据卡片配置）"""
    from db import get_db
    try:
        with get_db() as conn:
            cur = conn.cursor()
            # 获取所有活跃分支
            cur.execute("""
                SELECT pv.id, pv.name, pv.project_menu_id, m.name as project_name, pv.status
                FROM project_versions pv
                JOIN menus m ON pv.project_menu_id = m.id
                WHERE pv.status = 'active'
                ORDER BY m.name, pv.name
            """)
            rows = cur.fetchall()

            branches = [{'id': 'main', 'name': '主分支', 'projectMenuId': None, 'projectName': None}]
            for row in rows:
                branches.append({
                    'id': row[0],
                    'name': row[1],
                    'projectMenuId': row[2],
                    'projectName': row[3],
                    'status': row[4]
                })
            return jsonify(branches)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions', methods=['POST'])
@require_permission('admin.project_versions')
def create_version():
    """创建项目版本（快照或分支）"""
    body = request.get_json(force=True)
    project_menu_id = body.get('projectMenuId')
    name = body.get('name')
    description = body.get('description', '')
    version_type = body.get('versionType', 'snapshot')
    created_by = body.get('createdBy')
    parent_version = body.get('parentVersion')

    if not project_menu_id or not name or not created_by:
        return jsonify({'error': '缺少必要参数'}), 400

    if version_type not in ['snapshot', 'branch']:
        return jsonify({'error': 'versionType 必须是 snapshot 或 branch'}), 400

    try:
        result = create_project_version(
            project_menu_id, name, description, version_type,
            created_by, parent_version
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/diff', methods=['POST'])
@login_required
def diff_versions():
    """对比项目版本差异"""
    try:
        user_id = g.current_user.get('userId')
    except (AttributeError, KeyError):
        return jsonify({'error': '用户信息不完整'}), 401

    body = request.get_json(force=True)
    project_menu_id = body.get('projectMenuId')
    base_version = body.get('baseVersion')
    target_version = body.get('targetVersion')

    if not project_menu_id or not base_version or not target_version:
        return jsonify({'error': '缺少必要参数'}), 400

    try:
        result = compute_project_version_diff(
            project_menu_id, base_version, target_version, user_id
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/merge', methods=['POST'])
@require_permission('admin.project_versions')
def merge_version():
    """合并项目版本"""
    try:
        user_id = g.current_user.get('userId')
        username = g.current_user.get('username')
    except (AttributeError, KeyError):
        return jsonify({'error': '用户信息不完整'}), 401

    body = request.get_json(force=True)
    version_id = body.get('versionId')
    target_branch = body.get('targetBranch', 'current')
    strategy = body.get('strategy', 'theirs')
    project_menu_id = body.get('projectMenuId')
    skip_dependency_check = body.get('skipDependencyCheck', False)

    if not version_id or not project_menu_id:
        return jsonify({'error': '缺少必要参数'}), 400

    if strategy not in ['theirs', 'ours']:
        return jsonify({'error': 'strategy 必须是 theirs 或 ours'}), 400

    # 联合合并依赖检查
    if not skip_dependency_check:
        from utils.cross_project_dependency import check_merge_dependencies
        dep_check = check_merge_dependencies(project_menu_id, version_id)
        if not dep_check.get('canMerge'):
            return jsonify({
                'error': '存在阻塞依赖，无法合并',
                'dependencyCheck': dep_check,
            }), 400

    try:
        result = merge_project_version(
            version_id, target_branch, strategy,
            username, user_id, project_menu_id
        )

        # 合并成功后更新依赖声明并校验相关依赖
        if result.get('success'):
            from utils.cross_project_dependency import (
                batch_update_dependencies_after_merge,
                get_dependent_projects,
                validate_project_dependency,
            )
            batch_update_dependencies_after_merge(project_menu_id, version_id)

            # 校验依赖此项目的所有依赖声明
            dependents = get_dependent_projects(project_menu_id, 'main')
            for dep in dependents:
                try:
                    validate_project_dependency(dep['id'], send_notification=True)
                except Exception:
                    pass  # 校验失败不影响主流程

        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/merge-detailed', methods=['POST'])
@require_permission('admin.project_versions')
def merge_version_detailed():
    """合并项目版本（详细决策模式 - 支持按记录/字段选择）"""
    try:
        user_id = g.current_user.get('userId')
        username = g.current_user.get('username')
    except (AttributeError, KeyError):
        return jsonify({'error': '用户信息不完整'}), 401

    body = request.get_json(force=True)
    version_id = body.get('versionId')
    target_branch = body.get('targetBranch', 'current')
    project_menu_id = body.get('projectMenuId')
    collection_decisions = body.get('collections', [])
    skip_dependency_check = body.get('skipDependencyCheck', False)

    if not version_id or not project_menu_id:
        return jsonify({'error': '缺少必要参数'}), 400

    if not collection_decisions:
        return jsonify({'error': '没有选择任何变更'}), 400

    # 联合合并依赖检查
    if not skip_dependency_check:
        from utils.cross_project_dependency import check_merge_dependencies
        dep_check = check_merge_dependencies(project_menu_id, version_id)
        if not dep_check.get('canMerge'):
            return jsonify({
                'error': '存在阻塞依赖，无法合并',
                'dependencyCheck': dep_check,
            }), 400

    try:
        result = merge_project_version_detailed(
            version_id, target_branch, collection_decisions,
            username, user_id, project_menu_id
        )

        # 合并成功后更新依赖声明并校验相关依赖
        if result.get('success'):
            from utils.cross_project_dependency import (
                batch_update_dependencies_after_merge,
                get_dependent_projects,
                validate_project_dependency,
            )
            batch_update_dependencies_after_merge(project_menu_id, version_id)

            # 校验依赖此项目的所有依赖声明
            dependents = get_dependent_projects(project_menu_id, 'main')
            for dep in dependents:
                try:
                    validate_project_dependency(dep['id'], send_notification=True)
                except Exception:
                    pass  # 校验失败不影响主流程

        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<version_id>/merge-history', methods=['GET'])
@login_required
def get_version_merge_history(version_id):
    """获取版本的合并历史"""
    from db import get_db
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id, source_version_id, source_version_name, target_branch_id, target_branch_name, '
                'strategy, merged_by, merged_at, records_created, records_updated, records_deleted, description '
                'FROM merge_records WHERE source_version_id = %s ORDER BY merged_at DESC',
                (version_id,)
            )
            rows = cur.fetchall()
            merge_records = []
            for row in rows:
                merge_records.append({
                    'id': row[0],
                    'sourceVersionId': row[1],
                    'sourceVersionName': row[2],
                    'targetBranchId': row[3],
                    'targetBranchName': row[4],
                    'strategy': row[5],
                    'mergedBy': row[6],
                    'mergedAt': row[7].isoformat() if row[7] else None,
                    'recordsCreated': row[8],
                    'recordsUpdated': row[9],
                    'recordsDeleted': row[10],
                    'description': row[11],
                })
            return jsonify({'mergeRecords': merge_records, 'total': len(merge_records)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/merge-records/<project_menu_id>', methods=['GET'])
@login_required
def get_project_merge_records(project_menu_id):
    """获取项目的所有合并记录"""
    from db import get_db
    page = request.args.get('page', 1, type=int)
    pageSize = request.args.get('pageSize', 20, type=int)
    offset = (page - 1) * pageSize

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT COUNT(*) FROM merge_records WHERE project_menu_id = %s',
                (project_menu_id,)
            )
            total = cur.fetchone()[0]

            cur.execute(
                'SELECT id, source_version_id, source_version_name, target_branch_id, target_branch_name, '
                'strategy, merged_by, merged_at, records_created, records_updated, records_deleted, description '
                'FROM merge_records WHERE project_menu_id = %s ORDER BY merged_at DESC LIMIT %s OFFSET %s',
                (project_menu_id, pageSize, offset)
            )
            rows = cur.fetchall()
            merge_records = []
            for row in rows:
                merge_records.append({
                    'id': row[0],
                    'sourceVersionId': row[1],
                    'sourceVersionName': row[2],
                    'targetBranchId': row[3],
                    'targetBranchName': row[4],
                    'strategy': row[5],
                    'mergedBy': row[6],
                    'mergedAt': row[7].isoformat() if row[7] else None,
                    'recordsCreated': row[8],
                    'recordsUpdated': row[9],
                    'recordsDeleted': row[10],
                    'description': row[11],
                })
            return jsonify({'mergeRecords': merge_records, 'total': total, 'page': page, 'pageSize': pageSize})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<version_id>/lock', methods=['POST'])
@require_permission('admin.project_versions')
def lock_version(version_id):
    """锁定项目分支"""
    try:
        username = g.current_user.get('username')
    except (AttributeError, KeyError):
        return jsonify({'error': '用户信息不完整'}), 401

    body = request.get_json(force=True) or {}
    reason = body.get('reason')

    try:
        result = lock_project_version(version_id, username, reason)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<version_id>/unlock', methods=['POST'])
@require_permission('admin.project_versions')
def unlock_version(version_id):
    """解锁项目分支"""
    try:
        result = unlock_project_version(version_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/main/<project_menu_id>/lock', methods=['POST'])
@require_permission('admin.project_versions')
def lock_main_version(project_menu_id):
    """锁定项目的 main 分支"""
    try:
        username = g.current_user.get('username')
    except (AttributeError, KeyError):
        return jsonify({'error': '用户信息不完整'}), 401

    body = request.get_json(force=True) or {}
    reason = body.get('reason')

    try:
        result = lock_main_branch(project_menu_id, username, reason)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/main/<project_menu_id>/unlock', methods=['POST'])
@require_permission('admin.project_versions')
def unlock_main_version(project_menu_id):
    """解锁项目的 main 分支"""
    try:
        result = unlock_main_branch(project_menu_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 动态路由 ====================

# 详情路由必须在列表路由之前定义，避免被 <project_menu_id> 拦截
@project_versions_bp.route('/project-versions/detail/<version_id>', methods=['GET'])
@login_required
def get_version_detail(version_id):
    """获取项目版本详情"""
    try:
        result = get_project_version_detail(version_id)
        if not result:
            return jsonify({'error': '版本不存在'}), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<project_menu_id>/collections', methods=['GET'])
@login_required
def get_collections(project_menu_id):
    """获取项目下的所有collection列表"""
    try:
        collections = get_project_collections(project_menu_id)
        return jsonify({
            'collections': collections,
            'total': len(collections)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<project_menu_id>/current-branch', methods=['GET'])
@login_required
def get_current_branch(project_menu_id):
    """获取用户在项目的当前分支"""
    try:
        user_id = g.current_user.get('userId')
    except (AttributeError, KeyError):
        return jsonify({'branchId': 'main', 'branchName': 'main'})

    try:
        branch_id = get_user_project_branch(user_id, project_menu_id)
        branch_name = 'main' if branch_id == 'main' else branch_id

        from db import get_db
        with get_db() as conn:
            cur = conn.cursor()

            # 查询分支名称
            if branch_id != 'main':
                cur.execute(
                    'SELECT name FROM project_versions WHERE id = %s',
                    (branch_id,)
                )
                row = cur.fetchone()
                if row:
                    branch_name = row[0]

            # 查询 main 分支锁定状态
            cur.execute(
                'SELECT is_main_locked, main_locked_by FROM menus WHERE id = %s',
                (project_menu_id,)
            )
            main_row = cur.fetchone()
            main_locked = main_row and main_row[0]
            main_locked_by = main_row and main_row[1] if main_row else None

            result = {
                'branchId': branch_id,
                'branchName': branch_name,
                'mainLocked': main_locked or False,
                'mainLockedBy': main_locked_by,
            }

            # 如果当前在非 main 分支，也返回该分支的锁定状态
            if branch_id != 'main':
                cur.execute(
                    'SELECT is_locked, locked_by FROM project_versions WHERE id = %s',
                    (branch_id,)
                )
                version_row = cur.fetchone()
                if version_row:
                    result['currentLocked'] = version_row[0]
                    result['currentLockedBy'] = version_row[1]

            return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<project_menu_id>/current-branch', methods=['PUT'])
@write_required
def set_current_branch(project_menu_id):
    """设置用户在项目的当前分支"""
    try:
        user_id = g.current_user.get('userId')
        username = g.current_user.get('username')
    except (AttributeError, KeyError):
        return jsonify({'error': '用户信息不完整'}), 401

    body = request.get_json(force=True)
    branch_id = body.get('branchId', 'main')

    try:
        set_user_project_branch(user_id, username, project_menu_id, branch_id)
        return jsonify({
            'branchId': branch_id,
            'success': True
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<project_menu_id>', methods=['GET'])
@login_required
def list_versions(project_menu_id):
    """获取项目的版本列表"""
    page = request.args.get('page', 1, type=int)
    pageSize = request.args.get('pageSize', 20, type=int)

    try:
        result = list_project_versions(project_menu_id, page, pageSize)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<version_id>/switch', methods=['POST'])
@write_required
def switch_branch(version_id):
    """切换项目分支"""
    try:
        user_id = g.current_user.get('userId')
        username = g.current_user.get('username')
    except (AttributeError, KeyError):
        return jsonify({'error': '用户信息不完整'}), 401

    body = request.get_json(force=True)
    project_menu_id = body.get('projectMenuId')

    if not project_menu_id:
        return jsonify({'error': '缺少 projectMenuId'}), 400

    try:
        result = switch_project_branch(user_id, username, project_menu_id, version_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<version_id>', methods=['DELETE'])
@require_permission('admin.project_versions')
def delete_version(version_id):
    """删除项目版本"""
    try:
        delete_project_version(version_id)
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<version_id>/restore', methods=['POST'])
@require_permission('admin.project_versions')
def restore_version(version_id):
    """从项目版本恢复数据"""
    try:
        user_id = g.current_user.get('userId')
        username = g.current_user.get('username')
    except (AttributeError, KeyError):
        return jsonify({'error': '用户信息不完整'}), 401

    body = request.get_json(force=True)
    project_menu_id = body.get('projectMenuId')

    if not project_menu_id:
        return jsonify({'error': '缺少 projectMenuId'}), 400

    try:
        result = restore_from_project_version(
            version_id, username, user_id, project_menu_id
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<project_menu_id>/switch-main', methods=['POST'])
@write_required
def switch_to_main(project_menu_id):
    """切换项目到主分支"""
    try:
        user_id = g.current_user.get('userId')
        username = g.current_user.get('username')
    except (AttributeError, KeyError):
        return jsonify({'error': '用户信息不完整'}), 401

    try:
        result = switch_to_main_project_branch(user_id, username, project_menu_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<version_id>/delete-impact', methods=['GET'])
@require_permission('admin.project_versions')
def get_delete_impact(version_id):
    """获取删除项目版本的影响报告"""
    try:
        result = get_project_version_delete_impact(version_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500