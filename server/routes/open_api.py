import os
from flask import Blueprint, request, jsonify, g, send_file
from db import get_db
from auth import api_key_required
from config import OPEN_API_BRANCH
from datetime import timezone
from utils.mongo_query import translate as mongo_translate, remap_labels, MongoQueryError
from utils.search_text import compute_search_text
from routes.data_files import save_data_file
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


def file_in_public_collection(cur, file_id):
    """安全边界：仅当该文件被**某个 api_public 集合**的记录引用时，才允许经 Open API 访问。
    防止用 API Key 拉到挂在非公开集合上的文件。file_id 是 UUID，作为 JSON 字符串值出现，
    用子串匹配定位（下载属低频操作，可接受全表 ::text 扫描）。"""
    cur.execute(
        "SELECT 1 FROM dynamic_data dd "
        "JOIN page_configs pc ON pc.id = 'page-' || dd.collection "
        "WHERE pc.api_public = TRUE AND dd.data::text LIKE %s LIMIT 1",
        (f'%{file_id}%',),
    )
    return cur.fetchone() is not None


def enrich_file_urls(record, fields):
    """给 file / image 字段的每个文件对象补 `apiUrl`，指向 Open API 的下载端点，
    让外部调用方无需猜测内部 url 即可下载。就地修改并返回 record。"""
    file_fields = {f.get('fieldName') for f in (fields or [])
                   if f.get('controlType') in ('file', 'image')}
    for fname in file_fields:
        val = record.get(fname)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and item.get('uid'):
                    item['apiUrl'] = f"/api/v1/files/{item['uid']}/download"
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


def validate_branch_id(cur, branch_id):
    """Validate branch_id exists. Returns True if valid, False otherwise.
    'main' is always valid. For other branches, check project_versions table."""
    if branch_id == 'main':
        return True
    cur.execute(
        'SELECT id FROM project_versions WHERE id = %s AND status = %s',
        (branch_id, 'active')
    )
    return cur.fetchone() is not None


def get_request_branch_id(cur):
    """Get branch_id from request query params and validate it.
    Returns (branch_id, error_response) tuple. error_response is None if valid."""
    branch_id = request.args.get('branchId', OPEN_API_BRANCH)
    if not branch_id:
        branch_id = OPEN_API_BRANCH
    if branch_id != OPEN_API_BRANCH and not validate_branch_id(cur, branch_id):
        return None, (jsonify({'error': 'Branch not found or not active'}), 404)
    return branch_id, None


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


def check_primary_key_unique(cur, collection, data, pk_fields, exclude_id=None, branch_id=None):
    """Check if primary key combination is unique. Returns error message or None."""
    if branch_id is None:
        branch_id = OPEN_API_BRANCH
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


@open_api_bp.route('/branches', methods=['GET'])
@api_key_required
def list_branches():
    """List all active branches available for Open API queries."""
    with get_db() as conn:
        cur = conn.cursor()
        # Get all active branches from project_versions
        cur.execute(
            'SELECT id, name, description, project_menu_id, version_type, status, created_at '
            'FROM project_versions WHERE status = %s ORDER BY created_at DESC',
            ('active',)
        )
        rows = cur.fetchall()

    branches = [{'id': 'main', 'name': 'main', 'description': 'Default main branch', 'status': 'active'}]
    for row in rows:
        branches.append({
            'id': row[0],
            'name': row[1],
            'description': row[2],
            'projectMenuId': row[3],
            'versionType': row[4],
            'status': row[5],
            'createdAt': format_ts(row[6]),
        })
    return jsonify({'data': branches})


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

        # Get and validate branch_id
        branch_id, branch_error = get_request_branch_id(cur)
        if branch_error:
            return branch_error

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
            [collection, branch_id] + q_params,
        )
        total = cur.fetchone()[0]

        cur.execute(
            'SELECT id, collection, data, created_at FROM dynamic_data '
            'WHERE collection = %s AND branch_id = %s' + extra_where + ' ORDER BY created_at, id '
            'LIMIT %s OFFSET %s',
            [collection, branch_id] + q_params + [page_size, offset],
        )
        rows = cur.fetchall()
        fields = get_page_fields(cur, collection)

    return jsonify({
        'data': [enrich_file_urls(row_to_record(r), fields) for r in rows],
        'pagination': {
            'page': page,
            'pageSize': page_size,
            'total': total,
            'totalPages': (total + page_size - 1) // page_size if page_size > 0 else 0,
        },
        'branchId': branch_id,
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

        # Get and validate branch_id
        branch_id, branch_error = get_request_branch_id(cur)
        if branch_error:
            return branch_error

        cur.execute(
            'SELECT id, collection, data, created_at FROM dynamic_data '
            'WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, item_id, branch_id),
        )
        data_row = cur.fetchone()
        fields = get_page_fields(cur, collection) if data_row else []

    if not data_row:
        return jsonify({'error': 'Record not found'}), 404
    result = enrich_file_urls(row_to_record(data_row), fields)
    result['branchId'] = branch_id
    return jsonify({'data': result})


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

        # Get and validate branch_id
        branch_id, branch_error = get_request_branch_id(cur)
        if branch_error:
            return branch_error

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
            (record_id, branch_id),
        )
        if cur.fetchone():
            return jsonify({'error': 'Record ID already exists'}), 409

        # Check primary key uniqueness
        pk_fields = get_primary_key_fields(cur, collection)
        if pk_fields:
            pk_error = check_primary_key_unique(cur, collection, body, pk_fields, branch_id=branch_id)
            if pk_error:
                return jsonify({'error': pk_error}), 409

        # Strip internal fields
        data = {k: v for k, v in body.items() if k not in ('createdAt', 'updatedAt', '_version')}

        # statusBadge 字段：创建时若已带初始值，盖变化时间戳作为超时判定基准
        # （否则超时兜底任务永远匹配不到这条记录，安全网会有漏洞）
        from datetime import datetime
        for f in (fields or []):
            if f.get('controlType') == 'statusBadge' and data.get(f.get('fieldName')):
                data[f'_statusBadge_{f["fieldName"]}_changedAt'] = datetime.now(timezone.utc).isoformat()

        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, search_text) VALUES (%s, %s, %s, %s, %s) '
            'RETURNING id, collection, data, created_at',
            (record_id, collection, psycopg2.extras.Json(data), branch_id,
             compute_search_text(data, fields)),
        )
        new_row = cur.fetchone()

        # 旁路 create_item 的 API 写入后，重播种序列计数器，避免与后续创建重号。
        # 保留 API 提供的 autoSequence 值（不覆盖），仅把计数器抬到 GREATEST(计数, 已写入值)。
        from utils.sequences import reseed_sequences
        reseed_sequences(cur, collections=[collection])

    result = row_to_record(new_row)
    result['branchId'] = branch_id
    return jsonify({'data': result}), 201


@open_api_bp.route('/collections/<collection>/batch', methods=['POST'])
@api_key_required
def create_batch_items(collection):
    """Batch-create multiple records in a public writable collection.

    Create-only (no upsert): any record whose id already exists — either
    duplicated within the batch or already present in the database — counts
    as a failed record for that index, never overwrites an existing row.
    """
    MAX_BATCH_SIZE = 1000

    with get_db() as conn:
        cur = conn.cursor()

        # Check collection is public and writable
        error_resp = check_collection_writable(cur, collection)
        if error_resp:
            return error_resp

        # Get and validate branch_id
        branch_id, branch_error = get_request_branch_id(cur)
        if branch_error:
            return branch_error

        body = request.get_json(silent=True)
        if body is None:
            return jsonify({'error': 'Request body is required'}), 400

        records = body.get('records')
        if not records:
            return jsonify({'error': 'records is required'}), 400
        if len(records) > MAX_BATCH_SIZE:
            return jsonify({'error': f'Batch size exceeds maximum of {MAX_BATCH_SIZE} records'}), 400

        options = body.get('options', {})
        continue_on_error = options.get('continueOnError', False)

        fields = get_page_fields(cur, collection)
        pk_fields = get_primary_key_fields(cur, collection)

        # Duplicate ID detection within the batch: any id value that appears
        # more than once fails ALL of its occurrences (not just the 2nd+),
        # matching routes/dynamic.py::batch_create_items's convention.
        id_counts = {}
        for record in records:
            rid = record.get('id')
            if rid:
                id_counts[rid] = id_counts.get(rid, 0) + 1
        duplicate_id_values = {rid for rid, count in id_counts.items() if count > 1}

        # Batch-check which of the explicitly-provided ids already exist
        # (one query for the whole batch, not one per record).
        all_ids = list(id_counts.keys())
        existing_ids = set()
        if all_ids:
            cur.execute(
                'SELECT id FROM dynamic_data WHERE collection = %s AND id = ANY(%s) AND branch_id = %s',
                (collection, all_ids, branch_id),
            )
            existing_ids = set(row[0] for row in cur.fetchall())

        from datetime import datetime
        errors = []
        prepared = []

        for idx, record in enumerate(records):
            rid = record.get('id')

            if rid and rid in duplicate_id_values:
                errors.append({'index': idx, 'error': 'Duplicate ID in batch', 'record': record})
                continue

            req_errors = validate_required_fields(record, fields)
            if req_errors:
                errors.append({
                    'index': idx,
                    'error': 'Validation failed: ' + '; '.join(req_errors),
                    'record': record,
                })
                continue

            body_copy = dict(record)
            record_id = body_copy.pop('id', None) or f'api-{uuid.uuid4().hex[:12]}'

            if rid and rid in existing_ids:
                errors.append({'index': idx, 'error': 'Record ID already exists', 'record': record})
                continue

            if pk_fields:
                pk_error = check_primary_key_unique(cur, collection, body_copy, pk_fields, branch_id=branch_id)
                if pk_error:
                    errors.append({'index': idx, 'error': pk_error, 'record': record})
                    continue

            data = {k: v for k, v in body_copy.items() if k not in ('createdAt', 'updatedAt', '_version')}

            # statusBadge 字段：创建时若已带初始值，盖变化时间戳作为超时判定基准
            # （与 create_collection_item 单条创建的逻辑一致）
            for f in (fields or []):
                if f.get('controlType') == 'statusBadge' and data.get(f.get('fieldName')):
                    data[f'_statusBadge_{f["fieldName"]}_changedAt'] = datetime.now(timezone.utc).isoformat()

            prepared.append({'id': record_id, 'data': data})

        # continueOnError=false 且有任何一条失败：整批 400，不写入任何记录
        if errors and not continue_on_error:
            return jsonify({
                'error': 'Validation failed for one or more records',
                'failed': len(errors),
                'errors': errors,
            }), 400

        created_records = []
        if prepared:
            values = [
                (p['id'], collection, psycopg2.extras.Json(p['data']), branch_id,
                 compute_search_text(p['data'], fields))
                for p in prepared
            ]
            rows = psycopg2.extras.execute_values(
                cur,
                'INSERT INTO dynamic_data (id, collection, data, branch_id, search_text) VALUES %s '
                'RETURNING id, collection, data, created_at',
                values,
                fetch=True,
            )
            for row in rows:
                rec = row_to_record(row)
                rec['branchId'] = branch_id
                created_records.append(rec)

            from utils.sequences import reseed_sequences
            reseed_sequences(cur, collections=[collection])

    result = {
        'data': created_records,
        'created': len(created_records),
        'failed': len(errors),
    }
    if errors:
        result['errors'] = errors
    return jsonify(result), 201


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

        # Get and validate branch_id
        branch_id, branch_error = get_request_branch_id(cur)
        if branch_error:
            return branch_error

        body = request.get_json(silent=True)
        if not body:
            return jsonify({'error': 'Request body is required'}), 400

        # Check record exists
        cur.execute(
            'SELECT id, data, version FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, item_id, branch_id),
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

        # statusBadge 字段：值真正变化时记录变化时间戳，供超时兜底任务判定
        from datetime import datetime
        for field_cfg in (fields or []):
            if field_cfg.get('controlType') != 'statusBadge':
                continue
            fname = field_cfg.get('fieldName')
            if not fname or fname not in new_data:
                continue
            if old_data.get(fname) != merged.get(fname):
                merged[f'_statusBadge_{fname}_changedAt'] = datetime.now(timezone.utc).isoformat()

        # Validate required fields on the merged result
        req_errors = validate_required_fields(merged, fields)
        if req_errors:
            return jsonify({'error': 'Validation failed', 'details': req_errors}), 400

        # Check primary key uniqueness
        pk_fields = get_primary_key_fields(cur, collection)
        if pk_fields:
            pk_error = check_primary_key_unique(
                cur, collection, merged, pk_fields, exclude_id=item_id, branch_id=branch_id
            )
            if pk_error:
                return jsonify({'error': pk_error}), 409

        new_version = db_version + 1
        cur.execute(
            'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = %s, search_text = %s '
            'WHERE collection = %s AND id = %s AND version = %s AND branch_id = %s',
            (psycopg2.extras.Json(merged), new_version, compute_search_text(merged, fields),
             collection, item_id, db_version, branch_id),
        )
        if cur.rowcount == 0:
            return jsonify({
                'error': 'Record has been modified by another request, please retry',
                'code': 'VERSION_CONFLICT',
            }), 409

        cur.execute(
            'SELECT id, collection, data, created_at FROM dynamic_data '
            'WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, item_id, branch_id),
        )
        updated_row = cur.fetchone()

    result = row_to_record(updated_row)
    result['_version'] = new_version
    result['branchId'] = branch_id
    return jsonify({'data': result})


@open_api_bp.route('/files/<file_id>', methods=['GET'])
@api_key_required
def api_get_file_metadata(file_id):
    """文件元数据（名称/类型/大小）。仅当文件被公开集合引用时可见。"""
    with get_db() as conn:
        cur = conn.cursor()
        if not file_in_public_collection(cur, file_id):
            return jsonify({'error': 'File not found'}), 404
        cur.execute(
            'SELECT id, original_name, mime_type, size_bytes, uploaded_at '
            'FROM data_files WHERE id = %s',
            (file_id,),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': 'File not found'}), 404
    return jsonify({'data': {
        'id': row[0],
        'name': row[1],
        'mimeType': row[2],
        'size': row[3],
        'uploadedAt': format_ts(row[4]),
        'downloadUrl': f'/api/v1/files/{row[0]}/download',
    }})


@open_api_bp.route('/files/<file_id>/download', methods=['GET'])
@api_key_required
def api_download_file(file_id):
    """经 API Key 下载数据页文件/图片字段上传的文件。仅限被公开集合引用的文件。"""
    with get_db() as conn:
        cur = conn.cursor()
        if not file_in_public_collection(cur, file_id):
            return jsonify({'error': 'File not found'}), 404
        cur.execute(
            'SELECT original_name, mime_type, storage_path FROM data_files WHERE id = %s',
            (file_id,),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': 'File not found'}), 404
    name, mime, path = row
    if not os.path.isfile(path):
        return jsonify({'error': 'File missing on disk', 'code': 'STORAGE_MISSING'}), 410
    return send_file(
        path,
        mimetype=mime or 'application/octet-stream',
        download_name=name,
        as_attachment=True,
    )


@open_api_bp.route('/files', methods=['POST'])
@api_key_required
def api_upload_file():
    """经 API Key 上传文件，返回 uid 供随后写入记录的 file / image 字段。

    请求：multipart/form-data
      - `file`        : 要上传的文件（必填）
      - `collection`  : 目标数据页（必填，须为公开且可写的集合）

    返回 `{ data: { uid, name, size, mimeType, downloadUrl } }`。把
    `[{ "uid": <uid>, "name": <name>, "size": <size>, "type": <mimeType>,
        "url": "/api/data-files/<uid>/download" }]` 写入记录的 file/image
    字段即可建立引用。
    """
    collection = (request.form.get('collection') or '').strip()
    if not collection:
        return jsonify({'error': 'collection is required'}), 400

    with get_db() as conn:
        cur = conn.cursor()
        # 仅允许向「公开且可写」的集合上传，与写记录的鉴权一致
        err = check_collection_writable(cur, collection)
        if err:
            return err

    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'file is required'}), 400

    # API Key 无关联用户：uploaded_by 置空（该列可空）
    meta, err = save_data_file(f, uploaded_by=None)
    if err:
        return err

    return jsonify({'data': {
        'uid': meta['id'],
        'name': meta['name'],
        'size': meta['size'],
        'mimeType': meta['mimeType'],
        'downloadUrl': f"/api/v1/files/{meta['id']}/download",
    }}), 201
