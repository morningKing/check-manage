from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required, write_required
import uuid

comments_bp = Blueprint('comments', __name__)


@comments_bp.route('/comments/<collection>/<record_id>', methods=['GET'])
@login_required
def get_comments(collection, record_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, collection, record_id, content, mentions, author_id, author_name, created_at, updated_at '
            'FROM record_comments WHERE collection = %s AND record_id = %s ORDER BY created_at ASC',
            (collection, record_id)
        )
        rows = cur.fetchall()
    result = []
    for r in rows:
        result.append({
            'id': r[0], 'collection': r[1], 'recordId': r[2],
            'content': r[3], 'mentions': r[4] or [],
            'authorId': r[5], 'authorName': r[6],
            'createdAt': r[7].isoformat() if r[7] else None,
            'updatedAt': r[8].isoformat() if r[8] else None,
        })
    return jsonify(result)


@comments_bp.route('/comments/<collection>/<record_id>', methods=['POST'])
@write_required
def add_comment(collection, record_id):
    body = request.get_json(force=True)
    content = body.get('content', '').strip()
    if not content:
        return jsonify({"error": "评论内容不能为空"}), 400
    mentions = body.get('mentions', [])
    user = g.current_user
    comment_id = f'comment-{uuid.uuid4().hex[:12]}'
    import psycopg2.extras
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO record_comments (id, collection, record_id, content, mentions, author_id, author_name) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING created_at',
            (comment_id, collection, record_id, content,
             psycopg2.extras.Json(mentions),
             user.get('userId', ''), user.get('username', ''))
        )
        created_at = cur.fetchone()[0]
    # Notify mentioned users
    if mentions:
        try:
            from utils.notifier import notify_mention
            notify_mention(collection, record_id, mentions, user.get('username', ''))
        except Exception:
            pass
    return jsonify({
        'id': comment_id, 'collection': collection, 'recordId': record_id,
        'content': content, 'mentions': mentions,
        'authorId': user.get('userId', ''), 'authorName': user.get('username', ''),
        'createdAt': created_at.isoformat() if created_at else None,
        'updatedAt': created_at.isoformat() if created_at else None,
    }), 201


@comments_bp.route('/comments/<comment_id>', methods=['PUT'])
@write_required
def update_comment(comment_id):
    body = request.get_json(force=True)
    content = body.get('content', '').strip()
    if not content:
        return jsonify({"error": "评论内容不能为空"}), 400
    user = g.current_user
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT author_id FROM record_comments WHERE id = %s', (comment_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "评论不存在"}), 404
        if row[0] != user.get('userId') and user.get('role') != 'admin':
            return jsonify({"error": "无权编辑此评论"}), 403
        cur.execute(
            'UPDATE record_comments SET content = %s, updated_at = NOW() WHERE id = %s',
            (content, comment_id)
        )
    return jsonify({"id": comment_id, "content": content})


@comments_bp.route('/comments/<comment_id>', methods=['DELETE'])
@write_required
def delete_comment(comment_id):
    user = g.current_user
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT author_id FROM record_comments WHERE id = %s', (comment_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "评论不存在"}), 404
        if row[0] != user.get('userId') and user.get('role') != 'admin':
            return jsonify({"error": "无权删除此评论"}), 403
        cur.execute('DELETE FROM record_comments WHERE id = %s', (comment_id,))
    return jsonify({})
