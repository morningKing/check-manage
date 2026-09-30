from flask import Blueprint, request, jsonify
from db import get_db
from auth import login_required, require_permission
import psycopg2.extras
import uuid

trigger_rules_bp = Blueprint('trigger_rules', __name__)


def row_to_dict(row):
    return {
        'id': row[0], 'name': row[1], 'description': row[2],
        'enabled': row[3], 'sourceCollection': row[4],
        'triggerEvent': row[5], 'triggerCondition': row[6] or {},
        'targetCollection': row[7], 'actionType': row[8],
        'actionConfig': row[9] or {}, 'executionOrder': row[10],
        'createdAt': row[11].isoformat() if row[11] else None,
        'updatedAt': row[12].isoformat() if row[12] else None,
    }


@trigger_rules_bp.route('/triggerRules', methods=['GET'])
@login_required
def list_rules():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, description, enabled, source_collection, trigger_event, '
            'trigger_condition, target_collection, action_type, action_config, '
            'execution_order, created_at, updated_at FROM trigger_rules ORDER BY execution_order'
        )
        rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@trigger_rules_bp.route('/triggerRules/<rule_id>', methods=['GET'])
@login_required
def get_rule(rule_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, description, enabled, source_collection, trigger_event, '
            'trigger_condition, target_collection, action_type, action_config, '
            'execution_order, created_at, updated_at FROM trigger_rules WHERE id = %s',
            (rule_id,)
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row_to_dict(row))


@trigger_rules_bp.route('/triggerRules', methods=['POST'])
@require_permission('admin.trigger_rules')
def create_rule():
    body = request.get_json(force=True)
    rule_id = body.get('id') or f'rule-{uuid.uuid4().hex[:12]}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO trigger_rules (id, name, description, enabled, source_collection, '
            'trigger_event, trigger_condition, target_collection, action_type, action_config, execution_order) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (rule_id, body.get('name', ''), body.get('description', ''),
             body.get('enabled', True), body.get('sourceCollection', ''),
             body.get('triggerEvent', 'update'),
             psycopg2.extras.Json(body.get('triggerCondition', {})),
             body.get('targetCollection', ''), body.get('actionType', 'create'),
             psycopg2.extras.Json(body.get('actionConfig', {})),
             body.get('executionOrder', 0))
        )
    body['id'] = rule_id
    return jsonify(body), 201


@trigger_rules_bp.route('/triggerRules/<rule_id>', methods=['PUT'])
@require_permission('admin.trigger_rules')
def update_rule(rule_id):
    body = request.get_json(force=True)
    sets, params = [], []
    for key, col in [('name', 'name'), ('description', 'description'),
                      ('enabled', 'enabled'), ('sourceCollection', 'source_collection'),
                      ('triggerEvent', 'trigger_event'), ('targetCollection', 'target_collection'),
                      ('actionType', 'action_type'), ('executionOrder', 'execution_order')]:
        if key in body:
            sets.append(f'{col}=%s'); params.append(body[key])
    for key, col in [('triggerCondition', 'trigger_condition'), ('actionConfig', 'action_config')]:
        if key in body:
            sets.append(f'{col}=%s'); params.append(psycopg2.extras.Json(body[key]))
    sets.append('updated_at=NOW()')
    params.append(rule_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f'UPDATE trigger_rules SET {", ".join(sets)} WHERE id=%s', params)
    return jsonify(body)


@trigger_rules_bp.route('/triggerRules/<rule_id>', methods=['DELETE'])
@require_permission('admin.trigger_rules')
def delete_rule(rule_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM trigger_rules WHERE id = %s', (rule_id,))
    return jsonify({})


@trigger_rules_bp.route('/triggerRules/<rule_id>/logs', methods=['GET'])
@login_required
def get_rule_logs(rule_id):
    limit = min(int(request.args.get('limit', 50)), 200)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, rule_id, rule_name, source_collection, source_record_id, '
            'target_collection, target_record_id, status, error_message, created_at '
            'FROM trigger_logs WHERE rule_id = %s ORDER BY created_at DESC LIMIT %s',
            (rule_id, limit)
        )
        rows = cur.fetchall()
    result = []
    for r in rows:
        result.append({
            'id': r[0], 'ruleId': r[1], 'ruleName': r[2],
            'sourceCollection': r[3], 'sourceRecordId': r[4],
            'targetCollection': r[5], 'targetRecordId': r[6],
            'status': r[7], 'errorMessage': r[8],
            'createdAt': r[9].isoformat() if r[9] else None,
        })
    return jsonify(result)
