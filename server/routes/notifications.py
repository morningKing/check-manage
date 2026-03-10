from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required

notifications_bp = Blueprint('notifications', __name__)


@notifications_bp.route('/notifications', methods=['GET'])
@login_required
def list_notifications():
    user = g.current_user
    user_id = user.get('userId', '')
    unread_only = request.args.get('unreadOnly', 'false').lower() == 'true'
    limit = min(int(request.args.get('limit', 50)), 200)
    with get_db() as conn:
        cur = conn.cursor()
        if unread_only:
            cur.execute(
                'SELECT id, user_id, type, title, content, source_collection, source_record_id, is_read, created_at '
                'FROM notifications WHERE user_id = %s AND is_read = FALSE ORDER BY created_at DESC LIMIT %s',
                (user_id, limit)
            )
        else:
            cur.execute(
                'SELECT id, user_id, type, title, content, source_collection, source_record_id, is_read, created_at '
                'FROM notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT %s',
                (user_id, limit)
            )
        rows = cur.fetchall()
    result = []
    for r in rows:
        result.append({
            'id': r[0], 'userId': r[1], 'type': r[2], 'title': r[3],
            'content': r[4], 'sourceCollection': r[5], 'sourceRecordId': r[6],
            'isRead': r[7], 'createdAt': r[8].isoformat() if r[8] else None,
        })
    return jsonify(result)


@notifications_bp.route('/notifications/unread-count', methods=['GET'])
@login_required
def unread_count():
    user = g.current_user
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = FALSE',
            (user.get('userId', ''),)
        )
        count = cur.fetchone()[0]
    return jsonify({'count': count})


@notifications_bp.route('/notifications/<notif_id>/read', methods=['PUT'])
@login_required
def mark_read(notif_id):
    user = g.current_user
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE notifications SET is_read = TRUE WHERE id = %s AND user_id = %s',
            (notif_id, user.get('userId', ''))
        )
    return jsonify({})


@notifications_bp.route('/notifications/read-all', methods=['PUT'])
@login_required
def mark_all_read():
    user = g.current_user
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE notifications SET is_read = TRUE WHERE user_id = %s AND is_read = FALSE',
            (user.get('userId', ''),)
        )
    return jsonify({})
