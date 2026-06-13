"""
Cross-collection trigger engine.

Fires configured trigger rules when records are created/updated/deleted
in source collections, executing actions on target collections.
"""
import uuid
import psycopg2.extras
from datetime import datetime, timezone
from utils.notifier import create_notification


def fire_triggers(event, collection, record_id, old_data, new_data, operator, cur, operator_user_id=None):
    """
    Find and execute matching trigger rules.

    Args:
        event: 'create' | 'update' | 'delete'
        collection: source collection name
        record_id: source record ID
        old_data: previous record data (None for create)
        new_data: current record data (None for delete)
        operator: operator username string
        cur: database cursor (within existing transaction)
        operator_user_id: optional user ID for sending failure notifications

    Returns:
        list of trigger errors (empty if all succeeded)
    """
    trigger_errors = []
    try:
        cur.execute(
            'SELECT id, name, trigger_event, trigger_condition, target_collection, '
            'action_type, action_config, execution_order '
            'FROM trigger_rules WHERE source_collection = %s AND enabled = TRUE '
            'ORDER BY execution_order',
            (collection,)
        )
        rules = cur.fetchall()
    except Exception:
        return

    for rule in rules:
        rule_id, rule_name, trigger_event, trigger_condition, target_collection, \
            action_type, action_config, _ = rule

        # Check event match
        if trigger_event != event and trigger_event != 'fieldChange':
            continue

        # For fieldChange event, check specific field condition
        if trigger_event == 'fieldChange':
            if event != 'update':
                continue
            cond_field = (trigger_condition or {}).get('field')
            cond_value = (trigger_condition or {}).get('value')
            if cond_field:
                new_val = (new_data or {}).get(cond_field)
                old_val = (old_data or {}).get(cond_field)
                if new_val == old_val:
                    continue
                if cond_value is not None and str(new_val) != str(cond_value):
                    continue

        # Check simple field=value conditions for create/update events
        if trigger_event in ('create', 'update') and trigger_condition:
            source = new_data or {}
            cond_field = trigger_condition.get('field')
            cond_value = trigger_condition.get('value')
            if cond_field and cond_value is not None:
                if str(source.get(cond_field, '')) != str(cond_value):
                    continue

        # Execute action
        try:
            _execute_action(cur, action_type, action_config, target_collection,
                            new_data or {}, record_id, operator)
            _log_trigger(cur, rule_id, rule_name, collection, record_id, target_collection, None, 'success', None)
        except Exception as e:
            error_msg = str(e)
            _log_trigger(cur, rule_id, rule_name, collection, record_id, target_collection, None, 'error', error_msg)
            trigger_errors.append({
                'rule_id': rule_id,
                'rule_name': rule_name,
                'error': error_msg
            })

    # Notify operator if any trigger failed
    if trigger_errors and operator_user_id:
        for err in trigger_errors:
            create_notification(
                operator_user_id,
                'triggerError',
                f'触发器执行失败：{err["rule_name"]}',
                err['error'],
                collection,
                record_id
            )

    return trigger_errors


def _execute_action(cur, action_type, action_config, target_collection, source_data, source_id, operator):
    """Execute a single trigger action."""
    config = action_config or {}

    if action_type == 'create':
        mapping = config.get('fieldMapping', {})
        new_data = {}
        for target_field, source_expr in mapping.items():
            new_data[target_field] = _resolve_value(source_expr, source_data, source_id, operator)
        new_id = f'{target_collection[:8]}-{uuid.uuid4().hex[:12]}'
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data) VALUES (%s, %s, %s)',
            (new_id, target_collection, psycopg2.extras.Json(new_data))
        )
        # 闭合 autoSequence 计数器不变式：触发器创建的记录可能携带 autoSequence 编号，
        # 重播种 main（INSERT 未指定 branch_id → 默认 'main'），避免后续 create_item 重号。
        from utils.sequences import reseed_sequences
        reseed_sequences(cur, collections=[target_collection], branch_id='main')

    elif action_type == 'update':
        match_field = config.get('matchField')
        match_value = _resolve_value(config.get('matchValue', ''), source_data, source_id, operator)
        update_fields = config.get('updateFields', {})
        if not match_field or not match_value:
            return
        resolved_updates = {}
        for k, v in update_fields.items():
            resolved_updates[k] = _resolve_value(v, source_data, source_id, operator)
        # Find matching records and update
        cur.execute(
            "SELECT id, data FROM dynamic_data WHERE collection = %s AND data->>%s = %s",
            (target_collection, match_field, str(match_value))
        )
        for row in cur.fetchall():
            existing = row[1] or {}
            existing.update(resolved_updates)
            cur.execute(
                'UPDATE dynamic_data SET data = %s, updated_at = NOW() WHERE id = %s',
                (psycopg2.extras.Json(existing), row[0])
            )

    elif action_type == 'runScript':
        script_id = config.get('scriptId')
        if script_id:
            cur.execute('SELECT script FROM validation_scripts WHERE id = %s', (script_id,))
            script_row = cur.fetchone()
            if script_row and script_row[0]:
                from utils.script_runner import run_validation_script
                run_validation_script(script_row[0], source_data, 'trigger', {}, [], target_collection, cur.connection)


def _resolve_value(expr, source_data, source_id, operator):
    """Resolve a value expression: $source.field, $operator, $NOW, or literal."""
    if not isinstance(expr, str):
        return expr
    if expr.startswith('$source.'):
        field = expr[len('$source.'):]
        if field == 'id':
            return source_id
        return source_data.get(field, '')
    if expr == '$operator':
        return operator
    if expr == '$NOW':
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    return expr


def _log_trigger(cur, rule_id, rule_name, source_coll, source_id, target_coll, target_id, status, error):
    """Record trigger execution log."""
    try:
        log_id = f'tlog-{uuid.uuid4().hex[:12]}'
        cur.execute(
            'INSERT INTO trigger_logs (id, rule_id, rule_name, source_collection, source_record_id, '
            'target_collection, target_record_id, status, error_message) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (log_id, rule_id, rule_name, source_coll, source_id, target_coll, target_id, status, error)
        )
    except Exception:
        pass
