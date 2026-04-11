"""
版本管理 API 路由

提供版本快照的创建、查询、对比、合并、恢复等操作。
"""

from flask import Blueprint, request, jsonify, g as flask_g
from auth import login_required, write_required
from utils.version import (
    create_version_snapshot,
    get_version_list,
    get_version_detail,
    delete_version,
    load_version_data,
    load_current_data,
    compute_diff,
    merge_version_to_current,
    restore_from_version,
    switch_to_version,
    switch_to_main_branch,
    get_current_branch,
    get_user_current_branch,
    MAIN_BRANCH_ID,
    apply_partial_merge,
)
from utils.errors import MergeError
import logging

logger = logging.getLogger(__name__)

versions_bp = Blueprint('versions', __name__)


@versions_bp.route('/versions', methods=['GET'])
@login_required
def list_versions():
    """获取版本列表"""
    collection = request.args.get('collection')
    status = request.args.get('status')

    versions = get_version_list(collection=collection, status=status)
    return jsonify(versions)


@versions_bp.route('/versions', methods=['POST'])
@write_required
def create_version():
    """创建版本快照"""
    body = request.get_json(force=True)
    collection = body.get('collection')
    name = body.get('name')
    description = body.get('description', '')
    version_type = body.get('versionType', 'snapshot')
    parent_version = body.get('parentVersion')

    if not collection:
        return jsonify({'error': 'collection 是必填项'}), 400
    if not name:
        return jsonify({'error': 'name 是必填项'}), 400
    if version_type not in ('snapshot', 'branch'):
        return jsonify({'error': 'versionType 必须是 snapshot 或 branch'}), 400

    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    created_by = user.get('username', 'unknown')
    user_id = user.get('userId')

    # 获取用户当前分支，用于创建快照
    branch_id = get_user_current_branch(user_id, collection) if user_id else MAIN_BRANCH_ID

    try:
        result = create_version_snapshot(
            collection=collection,
            name=name,
            description=description,
            version_type=version_type,
            parent_version=parent_version,
            created_by=created_by,
            branch_id=branch_id,
        )
        return jsonify(result), 201
    except Exception as e:
        return jsonify({'error': f'创建版本失败: {str(e)}'}), 500


@versions_bp.route('/versions/<version_id>', methods=['GET'])
@login_required
def get_version(version_id):
    """获取版本详情"""
    version = get_version_detail(version_id)
    if not version:
        return jsonify({'error': '版本不存在'}), 404
    return jsonify(version)


@versions_bp.route('/versions/<version_id>', methods=['DELETE'])
@write_required
def delete_version_route(version_id):
    """删除版本"""
    try:
        success = delete_version(version_id, confirmed=True)
        if not success:
            return jsonify({'error': '版本不存在'}), 404
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@versions_bp.route('/versions/diff', methods=['POST'])
@login_required
def diff_versions():
    """对比两个版本或版本与当前数据的差异"""
    body = request.get_json(force=True)
    collection = body.get('collection')
    base_version = body.get('baseVersion')
    target_version = body.get('targetVersion')

    if not collection:
        return jsonify({'error': 'collection 是必填项'}), 400
    if not base_version or not target_version:
        return jsonify({'error': 'baseVersion 和 targetVersion 是必填项'}), 400
    if base_version == target_version:
        return jsonify({'error': '基准和对比版本不能相同'}), 400

    # 获取用户当前分支
    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    user_id = user.get('userId')
    current_branch_id = get_user_current_branch(user_id, collection) if user_id else MAIN_BRANCH_ID

    # 加载基准数据
    if base_version == 'current':
        base_records, base_rels = load_current_data(collection, branch_id=current_branch_id)
    else:
        # 检查是否为分支类型
        from db import get_db
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT version_type FROM collection_versions WHERE id = %s', (base_version,))
            row = cur.fetchone()
            if row and row[0] == 'branch':
                # 从 dynamic_data 加载分支数据
                base_records, base_rels = load_current_data(collection, branch_id=base_version)
            else:
                # 从 version_snapshots 加载快照数据
                base_records, base_rels = load_version_data(base_version)
        if base_records is None:
            return jsonify({'error': f'基准版本 {base_version} 不存在'}), 404

    # 加载对比数据
    if target_version == 'current':
        target_records, target_rels = load_current_data(collection, branch_id=current_branch_id)
    else:
        # 检查是否为分支类型
        from db import get_db
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT version_type FROM collection_versions WHERE id = %s', (target_version,))
            row = cur.fetchone()
            if row and row[0] == 'branch':
                # 从 dynamic_data 加载分支数据
                target_records, target_rels = load_current_data(collection, branch_id=target_version)
            else:
                # 从 version_snapshots 加载快照数据
                target_records, target_rels = load_version_data(target_version)
        if target_records is None:
            return jsonify({'error': f'对比版本 {target_version} 不存在'}), 404

    # 获取字段配置
    from db import get_db
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
        row = cur.fetchone()
    fields = row[0] if row and row[0] else []
    field_names = [f['fieldName'] for f in fields]
    relation_fields = [f for f in fields if f.get('controlType') == 'relation']

    # 计算差异
    diff = compute_diff(
        base_records, target_records, field_names,
        base_rels, target_rels, relation_fields
    )
    diff['fields'] = fields

    return jsonify(diff)


@versions_bp.route('/versions/merge', methods=['POST'])
@write_required
def merge_versions():
    """合并版本到当前数据"""
    body = request.get_json(force=True)
    source_version = body.get('sourceVersion')
    target_version = body.get('targetVersion', 'current')
    strategy = body.get('strategy', 'theirs')

    if not source_version:
        return jsonify({'error': 'sourceVersion 是必填项'}), 400
    if target_version != 'current':
        return jsonify({'error': '目前只支持合并到当前数据 (targetVersion = "current")'}), 400
    if strategy not in ('theirs', 'ours'):
        return jsonify({'error': 'strategy 必须是 theirs 或 ours'}), 400

    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    merged_by = user.get('username', 'unknown')
    user_id = user.get('userId')

    try:
        result = merge_version_to_current(
            version_id=source_version,
            strategy=strategy,
            merged_by=merged_by,
            user_id=user_id,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'合并失败: {str(e)}'}), 500


@versions_bp.route('/versions/partial-merge', methods=['POST'])
@write_required
def partial_merge_versions():
    """部分合并：根据用户决策选择性合并记录

    请求体:
        - source_version_id: 源版本 ID
        - target_branch: 目标分支 ID ('main' 表示主分支)
        - decisions: 用户合并决策
            - added_record_ids: 要新增的记录 ID 列表
            - removed_record_ids: 要删除的记录 ID 列表
            - modified_records: 要修改的记录列表
                - record_id: 记录 ID
                - field_values: 字段级合并结果 {fieldName: value}
    """
    body = request.get_json(force=True)
    source_version_id = body.get('source_version_id')
    target_branch = body.get('target_branch')
    decisions = body.get('decisions')

    # 验证必填字段
    if not source_version_id:
        return jsonify({'error': 'source_version_id 是必填项'}), 400
    if not target_branch:
        return jsonify({'error': 'target_branch 是必填项'}), 400
    if decisions is None:
        return jsonify({'error': 'decisions 是必填项'}), 400

    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    merged_by = user.get('username', 'unknown')
    user_id = user.get('userId')

    # 将 'current' 解析为用户的实际分支 ID
    if target_branch == 'current':
        # 需要先获取 collection 才能查询用户分支
        from db import get_db
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT collection FROM collection_versions WHERE id = %s', (source_version_id,))
            row = cur.fetchone()
        if not row:
            return jsonify({'error': '源版本不存在'}), 404
        merge_collection = row[0]
        actual_target_branch = get_user_current_branch(user_id, merge_collection) if user_id else MAIN_BRANCH_ID
    else:
        actual_target_branch = target_branch
        merge_collection = None  # 从 source_version 获取

    # 合并前自动创建快照（保护措施，失败不阻塞合并）
    snapshot_created = False
    try:
        from datetime import datetime
        # 获取源版本的 collection 用于创建快照
        if not merge_collection:
            from db import get_db
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute('SELECT collection FROM collection_versions WHERE id = %s', (source_version_id,))
                row = cur.fetchone()
            if row:
                merge_collection = row[0]
        if merge_collection:
            snapshot_branch_id = get_user_current_branch(user_id, merge_collection) if user_id else MAIN_BRANCH_ID
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
            create_version_snapshot(
                collection=merge_collection,
                name=f'合并前自动快照 - {timestamp}',
                description=f'合并版本 {source_version_id} 前自动创建的安全快照',
                version_type='snapshot',
                parent_version=None,
                created_by=merged_by,
                branch_id=snapshot_branch_id,
            )
            snapshot_created = True
    except Exception as snapshot_err:
        logger.warning('合并前自动快照创建失败: %s', snapshot_err)

    try:
        result = apply_partial_merge(
            source_version_id=source_version_id,
            target_branch=actual_target_branch,
            decisions=decisions,
            merged_by=merged_by,
        )
        return jsonify({
            'success': result['success'],
            'merged_count': result['merged_count'],
            'message': result['message'],
            'snapshot_created': snapshot_created,
        })
    except MergeError as e:
        return jsonify({'code': e.code, 'message': e.message}), 400
    except Exception as e:
        return jsonify({'error': f'部分合并失败: {str(e)}'}), 500


@versions_bp.route('/versions/<version_id>/restore', methods=['POST'])
@write_required
def restore_version(version_id):
    """从版本恢复数据"""
    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    restored_by = user.get('username', 'unknown')
    user_id = user.get('userId')

    try:
        result = restore_from_version(version_id, restored_by=restored_by, user_id=user_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': f'恢复失败: {str(e)}'}), 500


@versions_bp.route('/versions/<version_id>/switch', methods=['POST'])
@write_required
def switch_version(version_id):
    """切换到指定分支（数据分支化模式）

    切换后用户看到的是该分支的数据，不影响其他用户。
    """
    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    switched_by = user.get('username', 'unknown')
    user_id = user.get('userId')

    try:
        result = switch_to_version(version_id, switched_by=switched_by, user_id=user_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'切换失败: {str(e)}'}), 500


@versions_bp.route('/versions/switch-main', methods=['POST'])
@write_required
def switch_to_main():
    """切换到主分支"""
    collection = request.json.get('collection')
    if not collection:
        return jsonify({'error': 'collection 是必填项'}), 400

    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    switched_by = user.get('username', 'unknown')
    user_id = user.get('userId')

    try:
        result = switch_to_main_branch(collection, switched_by=switched_by, user_id=user_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'切换失败: {str(e)}'}), 500


@versions_bp.route('/versions/user-branch', methods=['GET'])
@login_required
def get_user_branch():
    """获取当前用户在指定集合的工作分支"""
    collection = request.args.get('collection')
    if not collection:
        return jsonify({'error': 'collection 是必填项'}), 400

    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    user_id = user.get('userId')

    branch_id = get_user_current_branch(user_id, collection)

    # 如果是主分支，返回 null
    if branch_id == MAIN_BRANCH_ID:
        return jsonify({
            'branchId': None,
            'branchName': '主分支',
        })

    # 否则获取分支详情
    branch_detail = get_version_detail(branch_id)
    return jsonify({
        'branchId': branch_id,
        'branchName': branch_detail.get('name') if branch_detail else branch_id,
    })


@versions_bp.route('/versions/current-branch', methods=['GET'])
@login_required
def get_current_branch_route():
    """获取集合的当前工作分支（已废弃，使用 /versions/user-branch）"""
    collection = request.args.get('collection')
    if not collection:
        return jsonify({'error': 'collection 是必填项'}), 400

    branch = get_current_branch(collection)
    return jsonify(branch)


