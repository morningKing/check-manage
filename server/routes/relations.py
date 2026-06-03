from flask import Blueprint, request, jsonify
from db import get_db
from auth import login_required, write_required
from utils.operation_log import log_operation, get_page_info, pick_display_name
from utils.version import get_user_current_branch, MAIN_BRANCH_ID
from flask import g as flask_g

relations_bp = Blueprint('relations', __name__)


def _get_current_user_branch(collection):
    """Get the current user's branch for a collection."""
    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    user_id = user.get('userId')
    if not user_id:
        return MAIN_BRANCH_ID
    return get_user_current_branch(user_id, collection)


@relations_bp.route('/relations/<collection>', methods=['GET'])
@login_required
def get_collection_relations(collection):
    """一次查询返回整个集合的所有关联关系，按 record_id → field_name 分组"""
    branch_id = _get_current_user_branch(collection)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT record_id, field_name, related_id FROM data_relations '
            'WHERE collection = %s AND branch_id = %s '
            'ORDER BY record_id, field_name',
            (collection, branch_id),
        )
        rows = cur.fetchall()

    result = {}
    for record_id, field_name, related_id in rows:
        if record_id not in result:
            result[record_id] = {}
        if field_name not in result[record_id]:
            result[record_id][field_name] = []
        result[record_id][field_name].append(related_id)

    return jsonify(result)


@relations_bp.route('/relations/<collection>/<record_id>', methods=['GET'])
@login_required
def get_relations(collection, record_id):
    branch_id = _get_current_user_branch(collection)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT field_name, related_id FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND branch_id = %s '
            'ORDER BY field_name',
            (collection, record_id, branch_id),
        )
        rows = cur.fetchall()

    result = {}
    for field_name, related_id in rows:
        if field_name not in result:
            result[field_name] = []
        result[field_name].append(related_id)

    return jsonify(result)


@relations_bp.route('/relations/<collection>/<record_id>/<field_name>', methods=['PUT'])
@write_required
def update_relations(collection, record_id, field_name):
    from utils.permissions import can_page
    from flask import g as _g
    role = (getattr(_g, 'current_user', {}) or {}).get('role')
    if not can_page(role, f'page-{collection}', 'update'):
        return jsonify({'error': '权限不足'}), 403
    body = request.get_json(force=True)
    target_collection = body['targetCollection']
    target_field = body['targetField']
    new_ids = set(body.get('ids', []))
    branch_id = _get_current_user_branch(collection)

    with get_db() as conn:
        cur = conn.cursor()

        # Look up source page info and record name for logging
        src_page_name, src_fields = get_page_info(cur, collection)
        field_label = field_name
        for f in src_fields:
            if f.get('fieldName') == field_name:
                field_label = f.get('label', field_name)
                break

        cur.execute('SELECT data FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s', (collection, record_id, branch_id))
        src_row = cur.fetchone()
        src_data = src_row[0] if src_row and src_row[0] else {}
        record_display = pick_display_name(src_data, src_fields) or record_id

        # Look up target page name
        tgt_page_name, _ = get_page_info(cur, target_collection)

        # 1. Get old related IDs (with branch filter)
        cur.execute(
            'SELECT related_id FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND field_name = %s AND branch_id = %s',
            (collection, record_id, field_name, branch_id),
        )
        old_ids = set(row[0] for row in cur.fetchall())

        # 2. Delete all existing forward relations for this field (with branch filter)
        cur.execute(
            'DELETE FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND field_name = %s AND branch_id = %s',
            (collection, record_id, field_name, branch_id),
        )

        # 3. Insert new forward relations (with branch_id)
        for rid in new_ids:
            cur.execute(
                'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
                'VALUES (%s, %s, %s, %s, %s, %s)',
                (collection, record_id, field_name, target_collection, rid, branch_id),
            )

        # 4. Sync reverse: remove reverse entries for removed IDs (with branch filter)
        removed = old_ids - new_ids
        for rid in removed:
            cur.execute(
                'DELETE FROM data_relations '
                'WHERE collection = %s AND record_id = %s AND field_name = %s AND related_id = %s AND branch_id = %s',
                (target_collection, rid, target_field, record_id, branch_id),
            )

        # 5. Sync reverse: add reverse entries for added IDs (with branch_id)
        added = new_ids - old_ids
        for rid in added:
            cur.execute(
                'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
                'VALUES (%s, %s, %s, %s, %s, %s) '
                'ON CONFLICT DO NOTHING',
                (target_collection, rid, target_field, collection, record_id, branch_id),
            )

    count = len(new_ids)
    log_operation('update', 'relation', record_id, record_display,
                  f'更新{src_page_name}「{record_display}」的 {field_label} → 关联 {count} 条{tgt_page_name}', branch_id=branch_id)
    return jsonify({"ids": list(new_ids)})


@relations_bp.route('/relations/<collection>/<record_id>', methods=['DELETE'])
@write_required
def delete_all_relations(collection, record_id):
    from utils.permissions import can_page
    from flask import g as _g
    role = (getattr(_g, 'current_user', {}) or {}).get('role')
    if not can_page(role, f'page-{collection}', 'update'):
        return jsonify({'error': '权限不足'}), 403
    branch_id = _get_current_user_branch(collection)
    with get_db() as conn:
        cur = conn.cursor()
        # Look up info for logging
        src_page_name, src_fields = get_page_info(cur, collection)
        cur.execute('SELECT data FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s', (collection, record_id, branch_id))
        src_row = cur.fetchone()
        src_data = src_row[0] if src_row and src_row[0] else {}
        record_display = pick_display_name(src_data, src_fields) or record_id

        cur.execute(
            'DELETE FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
            (collection, record_id, branch_id),
        )
        cur.execute(
            'DELETE FROM data_relations WHERE related_collection = %s AND related_id = %s AND branch_id = %s',
            (collection, record_id, branch_id),
        )
    log_operation('delete', 'relation', record_id, record_display,
                  f'删除{src_page_name}「{record_display}」的所有关联关系', branch_id=branch_id)
    return jsonify({})
