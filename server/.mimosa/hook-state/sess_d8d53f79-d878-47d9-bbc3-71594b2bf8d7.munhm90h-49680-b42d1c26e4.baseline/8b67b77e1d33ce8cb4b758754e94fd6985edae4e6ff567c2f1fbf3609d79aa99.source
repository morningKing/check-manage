from flask import Blueprint, jsonify, g as flask_g
from db import get_db
from auth import login_required
from utils.version import get_user_current_branch, MAIN_BRANCH_ID

timeline_bp = Blueprint('timeline', __name__)


def _get_current_user_branch(collection):
    """Get the current user's branch for a collection."""
    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    user_id = user.get('userId')
    if not user_id:
        return MAIN_BRANCH_ID
    return get_user_current_branch(user_id, collection)


@timeline_bp.route('/timeline/<collection>/<record_id>', methods=['GET'])
@login_required
def get_timeline(collection, record_id):
    """Merge comments and operation logs into a unified timeline."""
    branch_id = _get_current_user_branch(collection)
    items = []
    with get_db() as conn:
        cur = conn.cursor()

        # 构建分支ID到名称的映射
        cur.execute('SELECT id, name FROM collection_versions WHERE collection = %s', (collection,))
        branch_name_map = {row[0]: row[1] for row in cur.fetchall()}

        # 获取评论的作者ID
        cur.execute(
            'SELECT id, content, mentions, author_name, created_at, author_id '
            'FROM record_comments WHERE collection = %s AND record_id = %s',
            (collection, record_id)
        )
        for r in cur.fetchall():
            items.append({
                'type': 'comment',
                'id': r[0],
                'content': r[1],
                'mentions': r[2] or [],
                'author': r[3],
                'timestamp': r[4].isoformat() if r[4] else None,
                'authorId': r[5],
            })
        # Fetch operation logs (filtered by branch)
        cur.execute(
            'SELECT id, action, description, operator_name, created_at, field_changes, branch_id '
            'FROM operation_logs WHERE target_id = %s AND branch_id = %s ORDER BY created_at ASC',
            (record_id, branch_id),
        )
        for r in cur.fetchall():
            raw_branch_id = r[6]
            # 根据分支ID获取显示名称
            if raw_branch_id and raw_branch_id != 'main':
                branch_name = branch_name_map.get(raw_branch_id, raw_branch_id)
            else:
                branch_name = '主分支'
            item = {
                'type': 'statusChange' if r[5] and any(
                    fc.get('field', '').lower() in ('status', '状态')
                    for fc in (r[5] or [])
                ) else 'change',
                'id': r[0],
                'action': r[1],
                'content': r[2],
                'author': r[3],
                'timestamp': r[4].isoformat() if r[4] else None,
                'fieldChanges': r[5] or [],
                'branchName': branch_name,
            }
            items.append(item)
    # Sort by timestamp
    items.sort(key=lambda x: x.get('timestamp') or '')
    return jsonify(items)
