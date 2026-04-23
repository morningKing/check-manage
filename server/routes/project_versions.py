from flask import Blueprint, request, jsonify, g
from auth import login_required, admin_required, write_required
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

@project_versions_bp.route('/project-versions', methods=['POST'])
@admin_required
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
@admin_required
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

    if not version_id or not project_menu_id:
        return jsonify({'error': '缺少必要参数'}), 400

    if strategy not in ['theirs', 'ours']:
        return jsonify({'error': 'strategy 必须是 theirs 或 ours'}), 400

    try:
        result = merge_project_version(
            version_id, target_branch, strategy,
            username, user_id, project_menu_id
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    body = request.get_json(force=True)
    version_id = body.get('versionId')
    target_branch = body.get('targetBranch', 'current')
    strategy = body.get('strategy', 'theirs')
    project_menu_id = body.get('projectMenuId')

    if not version_id or not project_menu_id:
        return jsonify({'error': '缺少必要参数'}), 400

    if strategy not in ['theirs', 'ours']:
        return jsonify({'error': 'strategy 必须是 theirs 或 ours'}), 400

    try:
        result = merge_project_version(
            version_id, target_branch, strategy,
            username, user_id, project_menu_id
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@project_versions_bp.route('/project-versions/<version_id>/lock', methods=['POST'])
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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


@project_versions_bp.route('/project-versions/<version_id>', methods=['GET'])
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


@project_versions_bp.route('/project-versions/<version_id>', methods=['DELETE'])
@admin_required
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
@admin_required
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
@admin_required
def get_delete_impact(version_id):
    """获取删除项目版本的影响报告"""
    try:
        result = get_project_version_delete_impact(version_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500