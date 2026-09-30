from flask import Blueprint, request, jsonify
from db import get_db
from datetime import datetime, timezone
from auth import login_required, require_permission
from utils.operation_log import log_operation
import uuid

validation_scripts_bp = Blueprint('validation_scripts', __name__)


def format_ts(dt):
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def row_to_dict(row):
    return {
        'id': row[0],
        'name': row[1],
        'description': row[2],
        'script': row[3],
        'createdAt': format_ts(row[4]),
        'updatedAt': format_ts(row[5]),
    }


@validation_scripts_bp.route('/validationScripts', methods=['GET'])
@login_required
def list_scripts():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, description, script, created_at, updated_at '
            'FROM validation_scripts ORDER BY created_at'
        )
        rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@validation_scripts_bp.route('/validationScripts', methods=['POST'])
@require_permission('admin.validation_scripts')
def create_script():
    body = request.get_json(force=True)
    script_id = body.get('id') or f'vs-{uuid.uuid4().hex[:8]}'
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO validation_scripts (id, name, description, script, created_at, updated_at) '
            'VALUES (%s,%s,%s,%s,%s,%s)',
            (script_id, body.get('name', ''), body.get('description', ''),
             body.get('script', ''), now, now),
        )
    log_operation('create', 'validation_script', script_id, body.get('name', ''),
                  f'新增校验脚本「{body.get("name", "")}」')
    return jsonify({
        'id': script_id,
        'name': body.get('name', ''),
        'description': body.get('description', ''),
        'script': body.get('script', ''),
        'createdAt': format_ts(now),
        'updatedAt': format_ts(now),
    }), 201


@validation_scripts_bp.route('/validationScripts/<script_id>', methods=['PUT'])
@require_permission('admin.validation_scripts')
def update_script(script_id):
    body = request.get_json(force=True)
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        cur = conn.cursor()
        sets = ['updated_at=%s']
        params = [now]
        if 'name' in body:
            sets.append('name=%s')
            params.append(body['name'])
        if 'description' in body:
            sets.append('description=%s')
            params.append(body['description'])
        if 'script' in body:
            sets.append('script=%s')
            params.append(body['script'])
        params.append(script_id)
        cur.execute(
            f'UPDATE validation_scripts SET {", ".join(sets)} WHERE id=%s', params
        )
        cur.execute(
            'SELECT id, name, description, script, created_at, updated_at '
            'FROM validation_scripts WHERE id = %s', (script_id,)
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    result = row_to_dict(row)
    log_operation('update', 'validation_script', script_id, result['name'],
                  f'修改校验脚本「{result["name"]}」')
    return jsonify(result)


@validation_scripts_bp.route('/validationScripts/<script_id>', methods=['DELETE'])
@require_permission('admin.validation_scripts')
def delete_script(script_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name FROM validation_scripts WHERE id = %s', (script_id,))
        row = cur.fetchone()
        script_name = row[0] if row else script_id
        cur.execute('DELETE FROM validation_scripts WHERE id = %s', (script_id,))
        # Clear binding from all page_configs that reference this script
        cur.execute(
            'UPDATE page_configs SET validation_script = NULL WHERE validation_script = %s',
            (script_id,),
        )
    log_operation('delete', 'validation_script', script_id, script_name,
                  f'删除校验脚本「{script_name}」')
    return jsonify({})


@validation_scripts_bp.route('/validationScripts/<script_id>/test', methods=['POST'])
@require_permission('admin.validation_scripts')
def test_script(script_id):
    """Test a validation script with sample data."""
    body = request.get_json(force=True)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT script FROM validation_scripts WHERE id = %s', (script_id,),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    script_code = row[0]
    sample_record = body.get('record', {})
    sample_action = body.get('action', 'create')
    sample_old_data = body.get('oldData')
    sample_fields = body.get('fields', [])
    sample_collection = body.get('collection', 'test')

    try:
        from utils.script_runner import run_validation_script
        from db import pool
        test_conn = pool.getconn()
        try:
            errors, warnings, pending_relations = run_validation_script(
                script_code, sample_record, sample_action, sample_old_data,
                sample_fields, sample_collection, test_conn
            )
        finally:
            pool.putconn(test_conn)

        return jsonify({
            'success': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'pendingRelations': pending_relations,
        })
    except Exception as e:
        return jsonify({'success': False, 'errors': [str(e)], 'warnings': [], 'pendingRelations': []}), 400
