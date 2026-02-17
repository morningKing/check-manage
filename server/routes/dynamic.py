from flask import Blueprint, request, jsonify
from db import get_db
from datetime import datetime, timezone
from auth import login_required
from utils.operation_log import log_operation, get_page_info, pick_display_name, get_field_label_map
import psycopg2.extras

dynamic_bp = Blueprint('dynamic', __name__)

# Reserved paths that should not be handled by the dynamic catch-all
RESERVED = {'menus', 'pageConfigs', 'favicon.ico', 'relations', 'auth', 'users', 'operationLogs', 'backups', 'exportScripts', 'apiKeys', 'validationScripts'}


def format_ts(dt):
    """Format datetime to ISO 8601 with trailing Z (UTC)."""
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    s = dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    return s


def row_to_record(row):
    """Reconstruct flat object from (id, collection, data, created_at) row."""
    record = {'id': row[0]}
    if row[2]:  # data JSONB
        record.update(row[2])
    if row[3]:  # created_at
        record['createdAt'] = format_ts(row[3])
    return record


def get_primary_key_fields(cur, collection):
    """Get primary key field names from page config for a collection."""
    page_id = f'page-{collection}'
    cur.execute(
        'SELECT fields FROM page_configs WHERE id = %s', (page_id,)
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return []
    return [f['fieldName'] for f in row[0] if f.get('isPrimaryKey')]


def check_primary_key_unique(cur, collection, data, pk_fields, exclude_id=None):
    """Check if primary key combination is unique. Returns error message or None."""
    if not pk_fields:
        return None

    pk_values = {}
    for field in pk_fields:
        pk_values[field] = data.get(field)

    # Build JSONB conditions for each primary key field
    conditions = ['collection = %s']
    params = [collection]

    for field, value in pk_values.items():
        if value is None:
            conditions.append(f"(data->>%s IS NULL)")
            params.append(field)
        else:
            conditions.append(f"data->>%s = %s")
            params.append(field)
            params.append(str(value))

    if exclude_id:
        conditions.append('id != %s')
        params.append(exclude_id)

    sql = f"SELECT id FROM dynamic_data WHERE {' AND '.join(conditions)} LIMIT 1"
    cur.execute(sql, params)
    existing = cur.fetchone()

    if existing:
        labels = ', '.join(f'{f}={pk_values[f]}' for f in pk_fields)
        return f'主键重复：{labels}'
    return None


def get_validation_script(cur, collection):
    """Get validation script code from validation_scripts table via page_configs binding."""
    page_id = f'page-{collection}'
    cur.execute('SELECT validation_script FROM page_configs WHERE id = %s', (page_id,))
    row = cur.fetchone()
    script_id = row[0] if row and row[0] else None
    if not script_id:
        return None
    cur.execute('SELECT script FROM validation_scripts WHERE id = %s', (script_id,))
    script_row = cur.fetchone()
    return script_row[0] if script_row and script_row[0] else None


def apply_pending_relations(cur, collection, record_id, pending_relations):
    """Apply relation operations queued by validation script (bidirectional sync)."""
    for rel in pending_relations:
        field_name = rel['fieldName']
        target_collection = rel['targetCollection']
        target_field = rel['targetField']
        new_ids = set(rel['ids'])

        # Get old related IDs
        cur.execute(
            'SELECT related_id FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND field_name = %s',
            (collection, record_id, field_name),
        )
        old_ids = set(row[0] for row in cur.fetchall())

        # Delete all existing forward relations for this field
        cur.execute(
            'DELETE FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND field_name = %s',
            (collection, record_id, field_name),
        )

        # Insert new forward relations
        for rid in new_ids:
            cur.execute(
                'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id) '
                'VALUES (%s, %s, %s, %s, %s)',
                (collection, record_id, field_name, target_collection, rid),
            )

        # Sync reverse: remove reverse entries for removed IDs
        for rid in old_ids - new_ids:
            cur.execute(
                'DELETE FROM data_relations '
                'WHERE collection = %s AND record_id = %s AND field_name = %s AND related_id = %s',
                (target_collection, rid, target_field, record_id),
            )

        # Sync reverse: add reverse entries for added IDs
        for rid in new_ids - old_ids:
            cur.execute(
                'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id) '
                'VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                (target_collection, rid, target_field, collection, record_id),
            )


@dynamic_bp.route('/<collection>', methods=['GET'])
@login_required
def list_items(collection):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, collection, data, created_at FROM dynamic_data WHERE collection = %s ORDER BY created_at', (collection,))
        rows = cur.fetchall()
    return jsonify([row_to_record(r) for r in rows])


@dynamic_bp.route('/<collection>/<item_id>', methods=['GET'])
@login_required
def get_item(collection, item_id):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, collection, data, created_at FROM dynamic_data WHERE collection = %s AND id = %s', (collection, item_id))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row_to_record(row))


@dynamic_bp.route('/<collection>', methods=['POST'])
@login_required
def create_item(collection):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(force=True)
    rid = body.get('id')
    created_at = body.get('createdAt')
    data = {k: v for k, v in body.items() if k not in ('id', 'createdAt')}
    with get_db() as conn:
        cur = conn.cursor()
        # Check primary key uniqueness
        pk_fields = get_primary_key_fields(cur, collection)
        if pk_fields:
            error = check_primary_key_unique(cur, collection, data, pk_fields)
            if error:
                return jsonify({"error": error}), 409
        # Run validation script if configured
        page_name, fields = get_page_info(cur, collection)
        validation_script = get_validation_script(cur, collection)
        pending_relations = []
        if validation_script:
            from utils.script_runner import run_validation_script
            try:
                errors, warnings, pending_relations = run_validation_script(
                    validation_script, data, 'create', None, fields, collection, conn
                )
            except Exception as e:
                return jsonify({"error": f"校验脚本执行错误：{str(e)}"}), 400
            if errors:
                return jsonify({
                    "error": "校验失败",
                    "validationErrors": errors,
                    "validationWarnings": warnings,
                }), 400
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES (%s,%s,%s,%s)',
            (rid, collection, psycopg2.extras.Json(data), created_at),
        )
        # Apply relations queued by validation script
        if pending_relations:
            apply_pending_relations(cur, collection, rid, pending_relations)
        if not validation_script:
            page_name, fields = get_page_info(cur, collection)
    record_name = pick_display_name(data, fields) or rid
    log_operation('create', 'dynamic_data', rid, record_name,
                  f'新增{page_name}「{record_name}」')
    return jsonify(body), 201


@dynamic_bp.route('/<collection>/<item_id>', methods=['PUT'])
@login_required
def update_item(collection, item_id):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(force=True)
    created_at = body.get('createdAt')
    data = {k: v for k, v in body.items() if k not in ('id', 'createdAt')}
    with get_db() as conn:
        cur = conn.cursor()
        # Check primary key uniqueness (exclude current record)
        pk_fields = get_primary_key_fields(cur, collection)
        if pk_fields:
            error = check_primary_key_unique(cur, collection, data, pk_fields, exclude_id=item_id)
            if error:
                return jsonify({"error": error}), 409
        # Fetch old data for diff
        cur.execute('SELECT data FROM dynamic_data WHERE collection = %s AND id = %s', (collection, item_id))
        old_row = cur.fetchone()
        old_data = old_row[0] if old_row and old_row[0] else {}
        # Run validation script if configured
        page_name, fields = get_page_info(cur, collection)
        validation_script = get_validation_script(cur, collection)
        pending_relations = []
        if validation_script:
            from utils.script_runner import run_validation_script
            try:
                errors, warnings, pending_relations = run_validation_script(
                    validation_script, data, 'update', old_data, fields, collection, conn
                )
            except Exception as e:
                return jsonify({"error": f"校验脚本执行错误：{str(e)}"}), 400
            if errors:
                return jsonify({
                    "error": "校验失败",
                    "validationErrors": errors,
                    "validationWarnings": warnings,
                }), 400
        if created_at:
            cur.execute(
                'UPDATE dynamic_data SET data = %s, created_at = %s WHERE collection = %s AND id = %s',
                (psycopg2.extras.Json(data), created_at, collection, item_id),
            )
        else:
            cur.execute(
                'UPDATE dynamic_data SET data = %s WHERE collection = %s AND id = %s',
                (psycopg2.extras.Json(data), collection, item_id),
            )
        # Apply relations queued by validation script
        if pending_relations:
            apply_pending_relations(cur, collection, item_id, pending_relations)
        if not validation_script:
            page_name, fields = get_page_info(cur, collection)
    body['id'] = item_id
    label_map = get_field_label_map(fields)
    record_name = pick_display_name(data, fields) or pick_display_name(old_data, fields) or item_id
    changed_labels = []
    for key, new_val in data.items():
        if key in label_map and old_data.get(key) != new_val:
            changed_labels.append(label_map[key])
    if changed_labels:
        desc = f'修改{page_name}「{record_name}」的 {", ".join(changed_labels)}'
    else:
        desc = f'修改{page_name}「{record_name}」'
    log_operation('update', 'dynamic_data', item_id, record_name, desc)
    return jsonify(body)


def check_reference_dependencies(cur, collection, record_id):
    """Check if any records in other collections reference this record via 'reference' fields.

    Returns error message string if dependencies exist, None otherwise.
    """
    cur.execute('SELECT id, name, fields FROM page_configs')
    rows = cur.fetchall()

    for page_id, page_name, fields in rows:
        if not fields:
            continue
        child_collection = page_id.replace('page-', '')
        for field in fields:
            if field.get('controlType') != 'reference':
                continue
            ref_config = field.get('referenceConfig', {})
            if ref_config.get('targetCollection') != collection:
                continue
            # This page has a reference field pointing to our collection
            field_name = field['fieldName']
            cur.execute(
                "SELECT count(*) FROM dynamic_data WHERE collection = %s AND data->>%s = %s",
                (child_collection, field_name, record_id),
            )
            count = cur.fetchone()[0]
            if count > 0:
                return f'无法删除：被「{page_name}」的 {count} 条记录引用'
    return None


@dynamic_bp.route('/<collection>/<item_id>', methods=['DELETE'])
@login_required
def delete_item(collection, item_id):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    with get_db() as conn:
        cur = conn.cursor()
        # Check reference dependencies before deletion
        ref_error = check_reference_dependencies(cur, collection, item_id)
        if ref_error:
            return jsonify({"error": ref_error}), 409
        # Fetch record name for the log before deleting
        cur.execute('SELECT data FROM dynamic_data WHERE collection = %s AND id = %s', (collection, item_id))
        data_row = cur.fetchone()
        page_name, fields = get_page_info(cur, collection)
        if data_row and data_row[0]:
            record_name = pick_display_name(data_row[0], fields) or item_id
        else:
            record_name = item_id
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s AND id = %s', (collection, item_id))
        # Clean up all relations involving this record
        cur.execute(
            'DELETE FROM data_relations WHERE (collection = %s AND record_id = %s) OR (related_collection = %s AND related_id = %s)',
            (collection, item_id, collection, item_id),
        )
    log_operation('delete', 'dynamic_data', item_id, record_name,
                  f'删除{page_name}「{record_name}」')
    return jsonify({})
