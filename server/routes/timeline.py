from flask import Blueprint, jsonify
from db import get_db
from auth import login_required

timeline_bp = Blueprint('timeline', __name__)


@timeline_bp.route('/timeline/<collection>/<record_id>', methods=['GET'])
@login_required
def get_timeline(collection, record_id):
    """Merge comments and operation logs into a unified timeline."""
    items = []
    with get_db() as conn:
        cur = conn.cursor()
        # Fetch comments
        cur.execute(
            'SELECT id, content, mentions, author_name, created_at '
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
            })
        # Fetch operation logs
        cur.execute(
            'SELECT id, action, description, operator_name, created_at, field_changes '
            'FROM operation_logs WHERE target_id = %s ORDER BY created_at ASC',
            (record_id,)
        )
        for r in cur.fetchall():
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
            }
            items.append(item)
    # Sort by timestamp
    items.sort(key=lambda x: x.get('timestamp') or '')
    return jsonify(items)
