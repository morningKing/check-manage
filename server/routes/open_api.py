from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import api_key_required
from config import OPEN_API_BRANCH
from datetime import timezone
from utils.mongo_query import translate as mongo_translate, remap_labels, MongoQueryError
import uuid
import json
import psycopg2.extras

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


def check_collection_public(cur, page_id):
    """Check if collection is public. Returns (api_public, api_writable) or (None, None)."""
    cur.execute(
        'SELECT api_public, api_writable FROM page_configs WHERE id = %s', (page_id,)
    )
    row = cur.fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def check_collection_writable(cur, collection):
    """Check if collection is both public and writable. Returns error response or None."""
    page_id = f'page-{collection}'
    api_public, api_writable = check_collection_public(cur, page_id)
    if not api_public:
        return jsonify({'error': 'Collection not found or not public'}), 404
    if not api_writable:
        return jsonify({'error': 'Collection is read-only'}), 403
    return None


def get_page_fields(cur, collection):
    """Get fields config for a collection."""
    page_id = f'page-{collection}'
    cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
    row = cur.fetchone()
    return row[0] if row and row[0] else []


def validate_required_fields(data, fields):
    """Validate required fields are present. Returns list of error messages."""
    errors = []
    for f in fields:
        if not f.get('required'):
            continue
        field_name = f.get('fieldName')
        # Skip auto-generated fields
        if f.get('controlType') in ('autoSequence', 'autoTimestamp'):
            continue
        value = data.get(field_name)
        if value is None or (isinstance(value, str) and value.strip() == ''):
            label = f.get('label', field_name)
            errors.append(f'{label} is required')
    return errors


def get_primary_key_fields(cur, collection):
    """Get primary key field names from page config."""
    page_id = f'page-{collection}'
    cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
    row = cur.fetchone()
    if not row or not row[0]:
        return []
    return [f['fieldName'] for f in row[0] if f.get('isPrimaryKey')]


def check_primary_key_unique(cur, collection, data, pk_fields, exclude_id=None, branch_id=OPEN_API_BRANCH):
    """Check if primary key combination is unique. Returns error message or None."""
    if not pk_fields:
        return None
    pk_values = {}
    for field in pk_fields:
        pk_values[field] = data.get(field)

    conditions = ['collection = %s', 'branch_id = %s']
    params = [collection, branch_id]
    for field, value in pk_values.items():
        if value is None:
            conditions.append("(data->>%s IS NULL)")
            params.append(field)
        else:
            conditions.append("data->>%s = %s")
            params.append(field)
            params.append(str(value))
    if exclude_id:
        conditions.append('id != %s')
        params.append(exclude_id)

    sql = f"SELECT id FROM dynamic_data WHERE {' AND '.join(conditions)} LIMIT 1"
    cur.execute(sql, params)
    return f'Primary key conflict' if cur.fetchone() else None


@open_api_bp.route('/collections', methods=['GET'])
@api_key_required
def list_collections():
    """List all publicly accessible collections."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, description, api_writable '
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
            'writable': bool(row[3]),
        })
    return jsonify({'data': collections})


@open_api_bp.route('/collections/<collection>', methods=['GET'])
@api_key_required
def list_collection_data(collection):
    """List records in a public collection with pagination and optional MongoDB query."""
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        api_public, _ = check_collection_public(cur, page_id)
        if not api_public:
            return jsonify({'error': 'Collection not found or not public'}), 404

        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 20, type=int)
        page_size = min(page_size, 100)
        offset = (page - 1) * page_size

        query_str = request.args.get('q', '')
        extra_where = ''
        q_params = []

        if query_str:
            try:
                query = json.loads(query_str)
                # Remap labels to fieldNames
                cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
                pc_row = cur.fetchone()
                if pc_row and pc_row[0]:
                    query = remap_labels(query, pc_row[0])
                where_fragment, q_params = mongo_translate(query)
                extra_where = f' AND ({where_fragment})'
            except json.JSONDecodeError as e:
                return jsonify({'error': f'Query syntax error: {e}'}), 400
            except MongoQueryError as e:
                return jsonify({'error': f'Query syntax error: {e}'}), 400

        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s' + extra_where,
            [collection, OPEN_API_BRANCH] + q_params,
        )
        total = cur.fetchone()[0]

        cur.execute(
            'SELECT id, collection, data, created_at FROM dynamic_data '
            'WHERE collection = %s AND branch_id = %s' + extra_where + ' ORDER BY created_at '
            'LIMIT %s OFFSET %s',
            [collection, OPEN_API_BRANCH] + q_params + [page_size, offset],
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
        api_public, _ = check_collection_public(cur, page_id)
        if not api_public:
            return jsonify({'error': 'Collection not found or not public'}), 404

        cur.execute(
            'SELECT id, collection, data, created_at FROM dynamic_data '
            'WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, item_id, OPEN_API_BRANCH),
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
            'SELECT name, description, fields, api_public, api_writable '
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
            'writable': bool(row[4]),
            'fields': fields,
        },
    })


@open_api_bp.route('/collections/<collection>', methods=['POST'])
@api_key_required
def create_collection_item(collection):
    """Create a new record in a public writable collection."""
    with get_db() as conn:
        cur = conn.cursor()

        # Check collection is public and writable
        error_resp = check_collection_writable(cur, collection)
        if error_resp:
            return error_resp

        body = request.get_json(silent=True)
        if not body:
            return jsonify({'error': 'Request body is required'}), 400

        fields = get_page_fields(cur, collection)

        # Validate required fields
        req_errors = validate_required_fields(body, fields)
        if req_errors:
            return jsonify({'error': 'Validation failed', 'details': req_errors}), 400

        # Generate ID if not provided
        record_id = body.pop('id', None) or f'api-{uuid.uuid4().hex[:12]}'

        # Check ID uniqueness
        cur.execute(
            'SELECT id FROM dynamic_data WHERE id = %s AND branch_id = %s',
            (record_id, OPEN_API_BRANCH),
        )
        if cur.fetchone():
            return jsonify({'error': 'Record ID already exists'}), 409

        # Check primary key uniqueness
        pk_fields = get_primary_key_fields(cur, collection)
        if pk_fields:
            pk_error = check_primary_key_unique(cur, collection, body, pk_fields, branch_id=OPEN_API_BRANCH)
            if pk_error:
                return jsonify({'error': pk_error}), 409

        # Strip internal fields
        data = {k: v for k, v in body.items() if k not in ('createdAt', 'updatedAt', '_version')}

        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s) '
            'RETURNING id, collection, data, created_at',
            (record_id, collection, psycopg2.extras.Json(data), OPEN_API_BRANCH),
        )
        new_row = cur.fetchone()

    return jsonify({'data': row_to_record(new_row)}), 201


@open_api_bp.route('/collections/<collection>/<item_id>', methods=['PUT'])
@api_key_required
def update_collection_item(collection, item_id):
    """Update an existing record in a public writable collection."""
    with get_db() as conn:
        cur = conn.cursor()

        # Check collection is public and writable
        error_resp = check_collection_writable(cur, collection)
        if error_resp:
            return error_resp

        body = request.get_json(silent=True)
        if not body:
            return jsonify({'error': 'Request body is required'}), 400

        # Check record exists
        cur.execute(
            'SELECT id, data, version FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, item_id, OPEN_API_BRANCH),
        )
        existing = cur.fetchone()
        if not existing:
            return jsonify({'error': 'Record not found'}), 404

        old_data = existing[1] or {}
        db_version = existing[2] if existing[2] is not None else 1

        # Optimistic locking: check _version if client provides it
        client_version = body.pop('_version', None)
        if client_version is not None and int(client_version) != db_version:
            return jsonify({
                'error': 'Record has been modified by another request, please retry',
                'code': 'VERSION_CONFLICT',
                '_version': db_version,
            }), 409

        fields = get_page_fields(cur, collection)

        # Merge: old data + new data (partial update supported)
        merged = dict(old_data)
        new_data = {k: v for k, v in body.items() if k not in ('id', 'createdAt', 'updatedAt', '_version')}
        merged.update(new_data)

        # Validate required fields on the merged result
        req_errors = validate_required_fields(merged, fields)
        if req_errors:
            return jsonify({'error': 'Validation failed', 'details': req_errors}), 400

        # Check primary key uniqueness
        pk_fields = get_primary_key_fields(cur, collection)
        if pk_fields:
            pk_error = check_primary_key_unique(
                cur, collection, merged, pk_fields, exclude_id=item_id, branch_id=OPEN_API_BRANCH
            )
            if pk_error:
                return jsonify({'error': pk_error}), 409

        new_version = db_version + 1
        cur.execute(
            'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = %s '
            'WHERE collection = %s AND id = %s AND version = %s AND branch_id = %s',
            (psycopg2.extras.Json(merged), new_version, collection, item_id, db_version, OPEN_API_BRANCH),
        )
        if cur.rowcount == 0:
            return jsonify({
                'error': 'Record has been modified by another request, please retry',
                'code': 'VERSION_CONFLICT',
            }), 409

        cur.execute(
            'SELECT id, collection, data, created_at FROM dynamic_data '
            'WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, item_id, OPEN_API_BRANCH),
        )
        updated_row = cur.fetchone()

    result = row_to_record(updated_row)
    result['_version'] = new_version
    return jsonify({'data': result})
