"""
Workflow engine for status field transitions.

Validates state changes against configured workflow rules,
checks conditions and role permissions, and executes post-transition actions.
"""


def get_workflow_config(fields_config, field_name):
    """Find and return the workflowConfig for a given field."""
    for field in fields_config:
        if field.get('fieldName') == field_name:
            wf = field.get('workflowConfig')
            if wf and wf.get('enabled'):
                return wf
    return None


def find_transition(workflow_config, from_status, to_status):
    """Find a matching transition rule for from->to status change."""
    for t in workflow_config.get('transitions', []):
        if (t['from'] == from_status or t['from'] == '*') and t['to'] == to_status:
            return t
    return None


def check_conditions(conditions, record_data):
    """
    Check all conditions against record data.
    Returns (passed: bool, error_message: str or None)
    """
    if not conditions:
        return True, None

    for cond in conditions:
        field = cond.get('field', '')
        rule = cond.get('rule', '')
        value = record_data.get(field)

        if rule == 'notEmpty':
            if value is None or value == '' or value == []:
                return False, cond.get('message', f'字段 {field} 不能为空')
        elif rule == 'equals':
            if value != cond.get('value'):
                return False, cond.get('message', f'字段 {field} 值不匹配')
        elif rule == 'notEquals':
            if value == cond.get('value'):
                return False, cond.get('message', f'字段 {field} 值不应为 {cond.get("value")}')

    return True, None


def validate_transition(fields_config, field_name, from_status, to_status, record_data, user_role):
    """
    Validate if a status transition is allowed.

    Args:
        fields_config: list of field config dicts from page_configs.fields
        field_name: the status field name
        from_status: current status value
        to_status: target status value
        record_data: full record data dict
        user_role: current user's role string

    Returns:
        (allowed: bool, error: str or None, actions: list)
    """
    workflow_config = get_workflow_config(fields_config, field_name)
    if not workflow_config:
        # No workflow configured, allow all transitions
        return True, None, []

    transition = find_transition(workflow_config, from_status, to_status)
    if not transition:
        return False, f'不允许从「{from_status}」转换到「{to_status}」', []

    # Check role permissions
    allowed_roles = transition.get('roles', [])
    if allowed_roles and user_role not in allowed_roles:
        return False, f'您的角色（{user_role}）无权执行此操作', []

    # Check conditions
    passed, error = check_conditions(transition.get('conditions', []), record_data)
    if not passed:
        return False, error, []

    return True, None, transition.get('actions', [])


def execute_actions(actions, record_data, collection, record_id, cur):
    """
    Execute post-transition actions.

    Supports:
    - setField: set a field value ($NOW for current timestamp, $USER reserved for future)
    - runScript: execute a validation script

    Modifies record_data in place for setField actions.
    """
    from datetime import datetime, timezone

    if not actions:
        return

    for action in actions:
        action_type = action.get('type')

        if action_type == 'setField':
            field = action.get('field')
            value = action.get('value')
            if not field:
                continue

            if value == '$NOW':
                value = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')

            record_data[field] = value

        elif action_type == 'runScript':
            script_id = action.get('scriptId')
            if not script_id:
                continue
            try:
                cur.execute('SELECT script FROM validation_scripts WHERE id = %s', (script_id,))
                row = cur.fetchone()
                if row and row[0]:
                    from utils.script_runner import run_validation_script
                    run_validation_script(row[0], record_data, 'update', {}, [], collection, cur.connection)
            except Exception:
                pass  # Log but don't block the transition


def get_allowed_transitions(fields_config, field_name, current_status, user_role):
    """
    Get list of allowed target statuses from current status for a given role.
    Used by frontend to show available transition buttons.

    Returns list of {to, label} dicts.
    """
    workflow_config = get_workflow_config(fields_config, field_name)
    if not workflow_config:
        return []

    result = []
    for t in workflow_config.get('transitions', []):
        if t['from'] == current_status or t['from'] == '*':
            allowed_roles = t.get('roles', [])
            if not allowed_roles or user_role in allowed_roles:
                result.append({
                    'to': t['to'],
                    'label': t.get('label', t['to']),
                })
    return result
