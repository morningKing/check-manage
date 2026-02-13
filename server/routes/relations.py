from flask import Blueprint, request, jsonify
from db import get_db
from auth import login_required
from utils.operation_log import log_operation

relations_bp = Blueprint('relations', __name__)


@relations_bp.route('/relations/<collection>/<record_id>', methods=['GET'])
@login_required
def get_relations(collection, record_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT field_name, related_id FROM data_relations '
            'WHERE collection = %s AND record_id = %s '
            'ORDER BY field_name',
            (collection, record_id),
        )
        rows = cur.fetchall()

    result = {}
    for field_name, related_id in rows:
        if field_name not in result:
            result[field_name] = []
        result[field_name].append(related_id)

    return jsonify(result)


@relations_bp.route('/relations/<collection>/<record_id>/<field_name>', methods=['PUT'])
@login_required
def update_relations(collection, record_id, field_name):
    body = request.get_json(force=True)
    target_collection = body['targetCollection']
    target_field = body['targetField']
    new_ids = set(body.get('ids', []))

    with get_db() as conn:
        cur = conn.cursor()

        # 1. Get old related IDs
        cur.execute(
            'SELECT related_id FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND field_name = %s',
            (collection, record_id, field_name),
        )
        old_ids = set(row[0] for row in cur.fetchall())

        # 2. Delete all existing forward relations for this field
        cur.execute(
            'DELETE FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND field_name = %s',
            (collection, record_id, field_name),
        )

        # 3. Insert new forward relations
        for rid in new_ids:
            cur.execute(
                'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id) '
                'VALUES (%s, %s, %s, %s, %s)',
                (collection, record_id, field_name, target_collection, rid),
            )

        # 4. Sync reverse: remove reverse entries for removed IDs
        removed = old_ids - new_ids
        for rid in removed:
            cur.execute(
                'DELETE FROM data_relations '
                'WHERE collection = %s AND record_id = %s AND field_name = %s AND related_id = %s',
                (target_collection, rid, target_field, record_id),
            )

        # 5. Sync reverse: add reverse entries for added IDs
        added = new_ids - old_ids
        for rid in added:
            cur.execute(
                'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id) '
                'VALUES (%s, %s, %s, %s, %s) '
                'ON CONFLICT DO NOTHING',
                (target_collection, rid, target_field, collection, record_id),
            )

    log_operation('update', 'relation', record_id, field_name,
                  f'更新关联关系（集合：{collection}，记录：{record_id}，字段：{field_name}）')
    return jsonify({"ids": list(new_ids)})


@relations_bp.route('/relations/<collection>/<record_id>', methods=['DELETE'])
@login_required
def delete_all_relations(collection, record_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'DELETE FROM data_relations WHERE collection = %s AND record_id = %s',
            (collection, record_id),
        )
        cur.execute(
            'DELETE FROM data_relations WHERE related_collection = %s AND related_id = %s',
            (collection, record_id),
        )
    log_operation('delete', 'relation', record_id, None,
                  f'删除关联关系（集合：{collection}，记录：{record_id}）')
    return jsonify({})
