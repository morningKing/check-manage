"""门禁策略库 CRUD（2026-10-11 spec §8.1）。

读：登录用户（批创建下拉需要）；写：admin（门禁是治理语义，与
log_operation 留痕一致）。checks 全量过 validate_checks，非法 400 不带病
入库；PATCH 的 checks 显式传入才更新（哨兵语义，与批编辑一致）。
"""
from flask import Blueprint, g, jsonify, request

from auth import login_required, require_permission   # 与 ai_approvals 同款鉴权
from utils import gate_policy_repo
from utils.operation_log import log_operation

ai_gate_policies_bp = Blueprint('ai_gate_policies', __name__,
                                url_prefix='/ai/chat/gate-policies')


@ai_gate_policies_bp.get('')
@login_required
def list_policies():
    enabled = request.args.get('enabled')
    items = gate_policy_repo.list_policies(
        enabled=None if enabled is None else enabled.lower() == 'true')
    return jsonify({'policies': items})


@ai_gate_policies_bp.post('')
@require_permission('admin.ai_chat_admin')
def create_policy():
    body = request.get_json(silent=True) or {}
    try:
        pol = gate_policy_repo.create_policy(
            g.current_user['userId'], body.get('name'),
            body.get('description'), body.get('checks'),
            enabled=bool(body.get('enabled', True)))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    log_operation('create', 'gate_policy', pol['id'], pol['name'],
                  '创建门禁策略')
    return jsonify(pol), 201


@ai_gate_policies_bp.get('/<pid>')
@login_required
def get_policy(pid):
    pol = gate_policy_repo.get_policy(pid)
    if not pol:
        return jsonify({'error': 'not found'}), 404
    return jsonify(pol)


@ai_gate_policies_bp.patch('/<pid>')
@require_permission('admin.ai_chat_admin')
def update_policy(pid):
    body = request.get_json(silent=True) or {}
    try:
        pol = gate_policy_repo.update_policy(
            pid,
            name=body.get('name') if 'name' in body else None,
            description=body.get('description') if 'description' in body else None,
            checks=body.get('checks') if 'checks' in body else None,
            enabled=body.get('enabled') if 'enabled' in body else None)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not pol:
        return jsonify({'error': 'not found'}), 404
    log_operation('update', 'gate_policy', pid, pol.get('name'),
                  '更新门禁策略')
    return jsonify(pol)


@ai_gate_policies_bp.delete('/<pid>')
@require_permission('admin.ai_chat_admin')
def delete_policy(pid):
    try:
        deleted = gate_policy_repo.delete_policy(pid)
    except gate_policy_repo.PolicyInUseError as e:
        return jsonify({'error': str(e), 'code': 'POLICY_IN_USE'}), 409
    if not deleted:
        return jsonify({'error': 'not found'}), 404
    log_operation('delete', 'gate_policy', pid, None, '删除门禁策略')
    return jsonify({'deleted': True})
