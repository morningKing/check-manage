from flask import Blueprint, request, jsonify
from db import get_db
from auth import api_key_required
from datetime import timezone

open_api_bp = Blueprint('open_api', __name__, url_prefix='/api/v1')


def format_ts(dt):
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def row_to_record(row):
    record = {'id': row[0]}
    if row[2]:
        record.update(row[2])
    if row[3]:
        record['createdAt'] = format_ts(row[3])
    return record


@open_api_bp.route('/collections', methods=['GET'])
@api_key_required
def list_collections():
    """List all publicly accessible collections."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, description '
            'FROM page_configs WHERE api_public = TRUE ORDER BY created_at'
        )
        rows = cur.fetchall()

    collections = []
    for row in rows:
        collection = row[0].replace('page-', '')
        collections.append({
            'collection': collection,
            'name': row[1],
            'description': row[2],
        })
    return jsonify({'data': collections})


@open_api_bp.route('/collections/<collection>', methods=['GET'])
@api_key_required
def list_collection_data(collection):
    """List records in a public collection with pagination."""
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT api_public FROM page_configs WHERE id = %s', (page_id,)
        )
        row = cur.fetchone()
        if not row or not row[0]:
            return jsonify({'error': 'Collection not found or not public'}), 404

        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 20, type=int)
        page_size = min(page_size, 100)
        offset = (page - 1) * page_size

        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s',
            (collection,),
        )
        total = cur.fetchone()[0]

        cur.execute(
            'SELECT id, collection, data, created_at FROM dynamic_data '
            'WHERE collection = %s ORDER BY created_at '
            'LIMIT %s OFFSET %s',
            (collection, page_size, offset),
        )
        rows = cur.fetchall()

    return jsonify({
        'data': [row_to_record(r) for r in rows],
        'pagination': {
            'page': page,
            'pageSize': page_size,
            'total': total,
            'totalPages': (total + page_size - 1) // page_size if page_size > 0 else 0,
        },
    })


@open_api_bp.route('/collections/<collection>/<item_id>', methods=['GET'])
@api_key_required
def get_collection_item(collection, item_id):
    """Get a single record from a public collection."""
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT api_public FROM page_configs WHERE id = %s', (page_id,)
        )
        row = cur.fetchone()
        if not row or not row[0]:
            return jsonify({'error': 'Collection not found or not public'}), 404

        cur.execute(
            'SELECT id, collection, data, created_at FROM dynamic_data '
            'WHERE collection = %s AND id = %s',
            (collection, item_id),
        )
        data_row = cur.fetchone()

    if not data_row:
        return jsonify({'error': 'Record not found'}), 404
    return jsonify({'data': row_to_record(data_row)})


@open_api_bp.route('/collections/<collection>/schema', methods=['GET'])
@api_key_required
def get_collection_schema(collection):
    """Get field definitions for a public collection."""
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT name, description, fields, api_public '
            'FROM page_configs WHERE id = %s',
            (page_id,),
        )
        row = cur.fetchone()

    if not row or not row[3]:
        return jsonify({'error': 'Collection not found or not public'}), 404

    fields = []
    for f in (row[2] or []):
        fields.append({
            'fieldName': f.get('fieldName'),
            'label': f.get('label'),
            'type': f.get('controlType'),
            'required': f.get('required', False),
        })

    return jsonify({
        'data': {
            'collection': collection,
            'name': row[0],
            'description': row[1],
            'fields': fields,
        },
    })
