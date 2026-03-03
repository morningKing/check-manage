from flask import Blueprint, request, jsonify
from db import get_db
from datetime import datetime, timezone
from auth import login_required, write_required
from utils.operation_log import log_operation, get_page_info, pick_display_name, get_field_label_map
import psycopg2.extras

dynamic_bp = Blueprint('dynamic', __name__)

# Reserved paths that should not be handled by the dynamic catch-all
RESERVED = {'menus', 'pageConfigs', 'favicon.ico', 'relations', 'auth', 'users', 'operationLogs', 'backups', 'exportScripts', 'apiKeys', 'validationScripts', 'etlTasks'}


def format_ts(dt):
    """Format datetime to ISO 8601 with trailing Z (UTC)."""
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    s = dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    return s


def row_to_record(row):
    """Reconstruct flat object from (id, collection, data, created_at, updated_at, version) row."""
    record = {'id': row[0]}
    if row[2]:  # data JSONB
        record.update(row[2])
    if row[3]:  # created_at
        record['createdAt'] = format_ts(row[3])
    if row[4]:  # updated_at
        record['updatedAt'] = format_ts(row[4])
    record['_version'] = row[5] if len(row) > 5 and row[5] is not None else 1
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
        cur.execute('SELECT id, collection, data, created_at, updated_at, version FROM dynamic_data WHERE collection = %s ORDER BY created_at', (collection,))
        rows = cur.fetchall()
    return jsonify([row_to_record(r) for r in rows])


@dynamic_bp.route('/<collection>/<item_id>', methods=['GET'])
@login_required
def get_item(collection, item_id):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, collection, data, created_at, updated_at, version FROM dynamic_data WHERE collection = %s AND id = %s', (collection, item_id))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row_to_record(row))


@dynamic_bp.route('/<collection>', methods=['POST'])
@write_required
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
    body['_version'] = 1
    return jsonify(body), 201


@dynamic_bp.route('/<collection>/<item_id>', methods=['PUT'])
@write_required
def update_item(collection, item_id):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(force=True)
    created_at = body.get('createdAt')
    client_version = body.get('_version')
    data = {k: v for k, v in body.items() if k not in ('id', 'createdAt', '_version', 'updatedAt')}
    with get_db() as conn:
        cur = conn.cursor()
        # Check primary key uniqueness (exclude current record)
        pk_fields = get_primary_key_fields(cur, collection)
        if pk_fields:
            error = check_primary_key_unique(cur, collection, data, pk_fields, exclude_id=item_id)
            if error:
                return jsonify({"error": error}), 409
        # Fetch old data for diff (and current version for optimistic locking)
        cur.execute('SELECT data, version FROM dynamic_data WHERE collection = %s AND id = %s', (collection, item_id))
        old_row = cur.fetchone()
        if not old_row:
            return jsonify({"error": "记录不存在"}), 404
        old_data = old_row[0] if old_row[0] else {}
        db_version = old_row[1] if old_row[1] is not None else 1
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
        # Optimistic locking: check version if client provides it
        if client_version is not None and int(client_version) != db_version:
            return jsonify({
                "error": "数据已被其他用户修改，请刷新后重试",
                "code": "VERSION_CONFLICT",
                "_version": db_version,
            }), 409
        new_version = db_version + 1
        if created_at:
            cur.execute(
                'UPDATE dynamic_data SET data = %s, created_at = %s, updated_at = NOW(), version = %s '
                'WHERE collection = %s AND id = %s AND version = %s',
                (psycopg2.extras.Json(data), created_at, new_version, collection, item_id, db_version),
            )
        else:
            cur.execute(
                'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = %s '
                'WHERE collection = %s AND id = %s AND version = %s',
                (psycopg2.extras.Json(data), new_version, collection, item_id, db_version),
            )
        if cur.rowcount == 0:
            # Another request changed the version between our SELECT and UPDATE
            return jsonify({
                "error": "数据已被其他用户修改，请刷新后重试",
                "code": "VERSION_CONFLICT",
            }), 409
        # Apply relations queued by validation script
        if pending_relations:
            apply_pending_relations(cur, collection, item_id, pending_relations)
        if not validation_script:
            page_name, fields = get_page_info(cur, collection)
    body['id'] = item_id
    body['_version'] = new_version
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
@write_required
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


@dynamic_bp.route('/<collection>/batch-create', methods=['POST'])
@write_required
def batch_create_items(collection):
    """Batch create multiple records in a single transaction."""
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404

    body = request.get_json(force=True)
    records = body.get('records', [])
    options = body.get('options', {})

    if not records:
        return jsonify({"error": "records is required"}), 400

    skip_validation = options.get('skipValidation', False)
    continue_on_error = options.get('continueOnError', False)

    with get_db() as conn:
        cur = conn.cursor()

        # 1. Batch fetch configuration (shared by all records)
        page_name, fields = get_page_info(cur, collection)
        pk_fields = get_primary_key_fields(cur, collection)
        validation_script = None if skip_validation else get_validation_script(cur, collection)

        # 2. Extract all IDs for uniqueness check
        all_ids = [r.get('id') for r in records if r.get('id')]

        # 3. Check for duplicate IDs within the batch
        id_set = set()
        duplicate_ids = []
        for rid in all_ids:
            if rid in id_set:
                duplicate_ids.append(rid)
            id_set.add(rid)

        if duplicate_ids and not continue_on_error:
            return jsonify({
                "error": "批量导入包含重复 ID",
                "failed": len(records),
                "errors": [{"index": i, "error": "ID 重复", "record": records[i]}
                           for i, r in enumerate(records) if r.get('id') in duplicate_ids]
            }), 409

        # 4. Check existing IDs in database (batch query)
        if all_ids:
            cur.execute(
                'SELECT id FROM dynamic_data WHERE collection = %s AND id = ANY(%s)',
                (collection, all_ids)
            )
            existing_ids = set(row[0] for row in cur.fetchall())
        else:
            existing_ids = set()

        # 5. Validate and prepare records
        prepared_records = []
        errors = []
        sequence_values = {}

        for idx, record in enumerate(records):
            rid = record.get('id')
            data = record.get('data', {})
            relations = record.get('relations', {})

            # Skip records with duplicate IDs in batch
            if rid in duplicate_ids:
                errors.append({"index": idx, "error": "ID 重复", "record": record})
                continue

            # Skip records with existing IDs
            if rid and rid in existing_ids:
                errors.append({"index": idx, "error": "主键已存在", "record": record})
                continue

            # Check primary key uniqueness for composite keys
            if pk_fields:
                error = check_primary_key_unique(cur, collection, data, pk_fields)
                if error:
                    errors.append({"index": idx, "error": error, "record": record})
                    continue

            # Run validation script if configured
            if validation_script:
                from utils.script_runner import run_validation_script
                try:
                    val_errors, warnings, pending_relations = run_validation_script(
                        validation_script, data, 'create', None, fields, collection, conn
                    )
                    if val_errors:
                        errors.append({
                            "index": idx,
                            "error": "校验失败",
                            "validationErrors": val_errors,
                            "record": record
                        })
                        continue
                    # Store pending relations for later
                    record['_pending_relations'] = pending_relations
                except Exception as e:
                    errors.append({
                        "index": idx,
                        "error": f"校验脚本执行错误：{str(e)}",
                        "record": record
                    })
                    continue

            prepared_records.append({
                "id": rid,
                "data": data,
                "relations": relations,
                "index": idx
            })

        # 6. Handle errors based on continue_on_error flag
        if errors and not continue_on_error:
            return jsonify({
                "error": "批量创建失败",
                "failed": len(errors),
                "errors": errors
            }), 400

        # 7. Batch insert records using execute_values
        if prepared_records:
            values = [
                (r['id'], collection, psycopg2.extras.Json(r['data']))
                for r in prepared_records
            ]
            psycopg2.extras.execute_values(
                cur,
                'INSERT INTO dynamic_data (id, collection, data) VALUES %s',
                values
            )

            # 8. Batch insert relations (forward and reverse)
            all_relation_values = []
            all_reverse_values = []

            for record in prepared_records:
                rid = record['id']
                relations = record.get('relations', {})
                pending = record.get('_pending_relations', [])

                # Process relations from request
                for field_name, related_ids in relations.items():
                    if not isinstance(related_ids, list):
                        continue
                    for related_id in related_ids:
                        # Find field config to get target collection
                        field_config = next((f for f in fields if f['fieldName'] == field_name), None)
                        if not field_config:
                            continue
                        rel_config = field_config.get('relationConfig', {})
                        target_collection = rel_config.get('targetCollection')
                        target_field = rel_config.get('targetField')

                        if target_collection:
                            all_relation_values.append(
                                (collection, rid, field_name, target_collection, related_id)
                            )
                            if target_field:
                                all_reverse_values.append(
                                    (target_collection, related_id, target_field, collection, rid)
                                )

                # Process pending relations from validation script
                for rel in pending:
                    field_name = rel['fieldName']
                    target_collection = rel['targetCollection']
                    target_field = rel.get('targetField')
                    for related_id in rel['ids']:
                        all_relation_values.append(
                            (collection, rid, field_name, target_collection, related_id)
                        )
                        if target_field:
                            all_reverse_values.append(
                                (target_collection, related_id, target_field, collection, rid)
                            )

            # Batch insert forward relations
            if all_relation_values:
                psycopg2.extras.execute_values(
                    cur,
                    'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id) '
                    'VALUES %s ON CONFLICT DO NOTHING',
                    all_relation_values
                )

            # Batch insert reverse relations (bidirectional sync)
            if all_reverse_values:
                psycopg2.extras.execute_values(
                    cur,
                    'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id) '
                    'VALUES %s ON CONFLICT DO NOTHING',
                    all_reverse_values
                )

    # Log the batch creation
    if prepared_records:
        created_count = len(prepared_records)
        summary = f'{created_count} 条记录'
        log_operation('create', 'dynamic_data', ','.join(r['id'] for r in prepared_records[:3]),
                      summary, f'批量新增{page_name}「{summary}」')

    result = {
        "success": True,
        "created": len(prepared_records),
        "failed": len(errors),
        "sequenceValues": sequence_values
    }

    if errors:
        result["errors"] = errors

    return jsonify(result), 201


@dynamic_bp.route('/<collection>/batch-delete', methods=['POST'])
@write_required
def batch_delete_items(collection, **kwargs):
    """Batch delete multiple records in a single transaction."""
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    ids = request.json.get('ids', [])
    if not ids:
        return jsonify({"error": "ids is required"}), 400

    with get_db() as conn:
        cur = conn.cursor()
        page_name, fields = get_page_info(cur, collection)

        # Check reference dependencies for all IDs at once
        ref_fields = []
        cur.execute('SELECT id, name, fields FROM page_configs')
        rows = cur.fetchall()
        for page_id, pname, pfields in rows:
            if not pfields:
                continue
            child_collection = page_id.replace('page-', '')
            for field in pfields:
                if field.get('controlType') != 'reference':
                    continue
                ref_config = field.get('referenceConfig', {})
                if ref_config.get('targetCollection') == collection:
                    ref_fields.append((child_collection, field['fieldName'], pname))

        blocked_ids = {}
        for child_col, field_name, pname in ref_fields:
            cur.execute(
                "SELECT data->>%s FROM dynamic_data WHERE collection = %s AND data->>%s = ANY(%s)",
                (field_name, child_col, field_name, ids),
            )
            for (ref_id,) in cur.fetchall():
                if ref_id and ref_id in ids:
                    blocked_ids.setdefault(ref_id, []).append(pname)

        deletable_ids = [i for i in ids if i not in blocked_ids]

        deleted = 0
        record_names = []
        if deletable_ids:
            # Fetch record names for logging
            cur.execute(
                'SELECT id, data FROM dynamic_data WHERE collection = %s AND id = ANY(%s)',
                (collection, deletable_ids),
            )
            id_name_map = {}
            for row_id, data in cur.fetchall():
                id_name_map[row_id] = pick_display_name(data, fields) or row_id if data else row_id
            record_names = [id_name_map.get(i, i) for i in deletable_ids]

            # Batch delete records
            cur.execute(
                'DELETE FROM dynamic_data WHERE collection = %s AND id = ANY(%s)',
                (collection, deletable_ids),
            )
            deleted = cur.rowcount

            # Batch clean up relations
            cur.execute(
                'DELETE FROM data_relations WHERE (collection = %s AND record_id = ANY(%s)) OR (related_collection = %s AND related_id = ANY(%s))',
                (collection, deletable_ids, collection, deletable_ids),
            )

    if deleted > 0:
        summary = '、'.join(record_names[:3])
        if len(record_names) > 3:
            summary += f' 等{len(record_names)}条'
        log_operation('delete', 'dynamic_data', ','.join(deletable_ids[:3]), summary,
                      f'批量删除{page_name}「{summary}」')

    result = {"deleted": deleted}
    if blocked_ids:
        result["blocked"] = {rid: f'被「{"、".join(set(pages))}」引用' for rid, pages in blocked_ids.items()}
    return jsonify(result)
