"""
跨项目依赖 API 路由

端点：
- GET    /projects/:projectId/dependencies         # 获取项目的依赖列表
- POST   /projects/:projectId/dependencies         # 创建依赖声明
- PUT    /projects/:projectId/dependencies/:depId  # 更新依赖声明
- DELETE /projects/:projectId/dependencies/:depId  # 解除依赖声明
- POST   /dependencies/:depId/validate             # 触发依赖校验
- GET    /projects/:projectId/dependents           # 反向查询依赖方
- GET    /projects/:sourceId/scan-relations/:targetId  # 扫描项目间关联
- GET    /projects/:projectId/branches/:branchId/delete-check  # 分支删除保护检查
"""

from flask import Blueprint, request, jsonify, g
from auth import login_required, admin_required
from utils.cross_project_dependency import (
    create_project_dependency,
    get_project_dependencies,
    get_dependent_projects,
    get_dependency_by_id,
    update_project_dependency,
    delete_project_dependency,
    validate_project_dependency,
    scan_dependency_relations,
    get_dependency_relations,
    check_branch_delete_protection,
)

cross_project_deps_bp = Blueprint('cross_project_dependencies', __name__)


# ==================== 依赖声明管理 ====================

@cross_project_deps_bp.route('/projects/<project_menu_id>/dependencies', methods=['GET'])
@login_required
def list_dependencies(project_menu_id):
    """
    获取项目的依赖列表

    Query params:
    - branchId: 分支ID（可选）
    """
    branch_id = request.args.get('branchId')

    try:
        deps = get_project_dependencies(project_menu_id, branch_id)

        # 为每个依赖获取关联关系
        for dep in deps:
            dep['relations'] = get_dependency_relations(dep['id'])

        return jsonify({'dependencies': deps, 'total': len(deps)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@cross_project_deps_bp.route('/projects/<project_menu_id>/dependencies', methods=['POST'])
@admin_required
def create_dependency(project_menu_id):
    """
    创建依赖声明

    Body:
    {
        "sourceBranch": "main",
        "targetProject": "menu-2-2",
        "targetBranch": "main",
        "relationType": "track-main",
        "pinnedVersion": null
    }
    """
    try:
        username = g.current_user.get('username')
    except (AttributeError, KeyError):
        username = None

    body = request.get_json(force=True)
    source_branch = body.get('sourceBranch', 'main')
    target_project = body.get('targetProject')
    target_branch = body.get('targetBranch', 'main')
    relation_type = body.get('relationType')
    pinned_version = body.get('pinnedVersion')

    if not target_project or not relation_type:
        return jsonify({'error': '缺少必要参数: targetProject, relationType'}), 400

    if relation_type not in ['track-main', 'read-write', 'read-only']:
        return jsonify({'error': '无效的依赖类型，必须是 track-main, read-write 或 read-only'}), 400

    try:
        result = create_project_dependency(
            project_menu_id, source_branch,
            target_project, target_branch,
            relation_type, pinned_version,
            username
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@cross_project_deps_bp.route('/projects/<project_menu_id>/dependencies/<dependency_id>', methods=['PUT'])
@admin_required
def update_dependency(project_menu_id, dependency_id):
    """
    更新依赖声明

    Body:
    {
        "targetBranch": "feat-new",
        "relationType": "read-write"
    }
    """
    body = request.get_json(force=True)

    # 转换字段名（前端驼峰 → 后端蛇形）
    updates = {}
    if 'targetBranch' in body:
        updates['target_branch'] = body['targetBranch']
    if 'relationType' in body:
        updates['relation_type'] = body['relationType']
    if 'pinnedVersion' in body:
        updates['pinned_version'] = body['pinnedVersion']

    try:
        result = update_project_dependency(dependency_id, **updates)

        # 获取关联关系
        result['relations'] = get_dependency_relations(dependency_id)

        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@cross_project_deps_bp.route('/projects/<project_menu_id>/dependencies/<dependency_id>', methods=['DELETE'])
@admin_required
def delete_dependency(project_menu_id, dependency_id):
    """
    解除依赖声明
    """
    try:
        success = delete_project_dependency(dependency_id)
        return jsonify({'success': success})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 依赖校验 ====================

@cross_project_deps_bp.route('/dependencies/<dependency_id>/validate', methods=['POST'])
@admin_required
def validate_dependency(dependency_id):
    """
    触发依赖校验
    """
    try:
        result = validate_project_dependency(dependency_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 依赖详情 ====================

@cross_project_deps_bp.route('/dependencies/<dependency_id>', methods=['GET'])
@login_required
def get_dependency_detail(dependency_id):
    """
    获取依赖声明详情
    """
    try:
        dep = get_dependency_by_id(dependency_id)
        if not dep:
            return jsonify({'error': '依赖声明不存在'}), 404

        dep['relations'] = get_dependency_relations(dependency_id)
        return jsonify(dep)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 反向查询 ====================

@cross_project_deps_bp.route('/projects/<project_menu_id>/dependents', methods=['GET'])
@login_required
def list_dependents(project_menu_id):
    """
    获取依赖于此项目的项目列表
    """
    branch_id = request.args.get('branchId')

    try:
        dependents = get_dependent_projects(project_menu_id, branch_id)

        # 为每个依赖获取关联关系
        for dep in dependents:
            dep['relations'] = get_dependency_relations(dep['id'])

        return jsonify({'dependents': dependents, 'total': len(dependents)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 关联关系扫描 ====================

@cross_project_deps_bp.route('/projects/<source_project>/scan-relations/<target_project>', methods=['GET'])
@login_required
def scan_relations(source_project, target_project):
    """
    扫描源项目与目标项目之间的关联关系
    """
    try:
        relations = scan_dependency_relations(source_project, target_project)
        return jsonify({'relations': relations, 'total': len(relations)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 分支删除保护 ====================

@cross_project_deps_bp.route('/projects/<project_menu_id>/branches/<branch_id>/delete-check', methods=['GET'])
@admin_required
def check_delete_protection(project_menu_id, branch_id):
    """
    检查分支是否可删除（依赖保护）
    """
    try:
        result = check_branch_delete_protection(project_menu_id, branch_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500