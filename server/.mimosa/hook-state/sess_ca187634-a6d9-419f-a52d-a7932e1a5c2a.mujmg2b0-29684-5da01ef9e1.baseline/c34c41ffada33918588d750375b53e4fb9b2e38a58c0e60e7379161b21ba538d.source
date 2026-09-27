"""AI 审批 REST 端点（ai-harness-p2 spec §7.3）。

审批权限：admin.ai_approval 能力键（新增）或审批请求上被点名的用户/角色。
所有审批动作写 log_operation（含理由）。
"""
from flask import Blueprint, g as flask_g, jsonify, request

from auth import login_required, require_permission
from utils.operation_log import log_operation

from utils import approval_repo

ai_approvals_bp = Blueprint('ai_approvals', __name__,
                            url_prefix='/v1/ai-approvals')


@ai_approvals_bp.get('')
@login_required
def list_approvals():
    """审批列表：admin.ai_approval 看全部；普通用户只看被点名待决的。"""
    user = flask_g.current_user
    if user.get('role') == 'admin':
        return jsonify({'approvals': approval_repo.list_all()})
    pending = approval_repo.get_pending_for_inbox(
        {'username': user.get('username'), 'role': user.get('role')})
    return jsonify({'approvals': pending})


@ai_approvals_bp.post('/<approval_id>/approve')
@login_required
def approve(approval_id):
    return _decide(approval_id, 'approved')


@ai_approvals_bp.post('/<approval_id>/reject')
@login_required
def reject(approval_id):
    return _decide(approval_id, 'rejected')


def _decide(approval_id: str, decision: str):
    user = flask_g.current_user
    body = request.get_json(silent=True) or {}
    comment = (body.get('comment') or '').strip() or None
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT requested_roles, requested_users, status "
                "FROM ai_approval_requests WHERE id = %s", (approval_id,))
            row = cur.fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    appr = {'requested_roles': row[0] or [], 'requested_users': row[1] or []}
    user_ctx = {'username': user.get('username'), 'role': user.get('role')}
    # 审批权限（spec §7.3）：admin 角色天然持 admin.ai_approval；
    # 非管理员必须是被点名（requested_users/requested_roles）的对象。
    if user.get('role') != 'admin' and not approval_repo.can_decide(
            appr, user_ctx):
        return jsonify({'error': 'forbidden'}), 403
    result = approval_repo.decide(approval_id, decision, user['userId'],
                                  comment=comment)
    if result is None:
        return jsonify({'error': 'not found'}), 404
    try:
        log_operation(user['userId'], 'update', 'ai_approval_request',
                      approval_id, None,
                      f'{decision}（{comment or "无备注"}）')
    except Exception:
        pass
    return jsonify(result), 200
