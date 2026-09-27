"""
Operation logging helper.

Provides log_operation() to record audit entries.
All exceptions are caught internally -- logging failure must never
break the primary business operation.
"""
import uuid
from urllib.parse import unquote
from flask import g, has_request_context, request
from db import get_db


def get_page_info(cur, collection):
    """获取集合对应的页面名称和字段配置，用于丰富日志描述。

    Returns:
        (page_name, fields) — page_name 回退为 collection, fields 回退为 []
    """
    page_id = f'page-{collection}'
    cur.execute('SELECT name, fields FROM page_configs WHERE id = %s', (page_id,))
    row = cur.fetchone()
    if not row:
        return collection, []
    return row[0] or collection, row[1] or []


def pick_display_name(data, fields=None):
    """从数据中选取最佳显示名称（优先按字段顺序取第一个文本字段）。"""
    if fields:
        for f in sorted(fields, key=lambda x: x.get('order', 999)):
            if f.get('controlType') in ('text', 'textarea', 'autoSequence'):
                val = data.get(f.get('fieldName', ''))
                if val and isinstance(val, str):
                    return val
    for key in ('name', 'caseName', 'planName', 'specialName'):
        val = data.get(key)
        if val and isinstance(val, str):
            return val
    return None


def get_field_label_map(fields):
    """构建 fieldName → label 的映射。"""
    return {f['fieldName']: f.get('label', f['fieldName']) for f in fields}


def _write_log_row(operator_id, operator_name, operator_role, action, target_type,
                   target_id, target_name, description, field_changes=None,
                   branch_id=None, api_key_id=None):
    """Shared INSERT for log_operation (JWT/UI actor) and log_api_operation
    (API Key actor) — both resolve to the same operator_* triple + description,
    they just source it differently. Never raises: logging failure must never
    break the primary business operation."""
    try:
        log_id = f'log-{uuid.uuid4().hex[:12]}'

        batch_id = None
        batch_desc = None
        if has_request_context():
            batch_id = request.headers.get('X-Batch-Id') or None
            raw_desc = request.headers.get('X-Batch-Desc') or None
            batch_desc = unquote(raw_desc) if raw_desc else None

        import psycopg2.extras
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO operation_logs '
                '(id, action, target_type, target_id, target_name, description, '
                ' operator_id, operator_name, operator_role, batch_id, batch_desc, '
                ' field_changes, branch_id, api_key_id) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (log_id, action, target_type, target_id, target_name, description,
                 operator_id, operator_name, operator_role, batch_id, batch_desc,
                 psycopg2.extras.Json(field_changes) if field_changes else None,
                 branch_id or 'main', api_key_id),
            )
    except Exception:
        pass


def log_operation(action, target_type, target_id, target_name, description, field_changes=None, branch_id=None):
    """
    Record an operation log entry for a JWT/UI-authenticated action.

    Args:
        action:       'create' | 'update' | 'delete'
        target_type:  'menu' | 'page_config' | 'dynamic_data' | 'user' | 'relation'
        target_id:    ID of the affected record (str or None)
        target_name:  Human-readable name of the target (str or None)
        description:  Chinese human-readable description
        field_changes: Optional list of {field, label, from, to} dicts
        branch_id:    Optional branch ID for branch-specific logging
    """
    try:
        user = getattr(g, 'current_user', None)
    except RuntimeError:
        # Called outside a Flask app/request context (e.g. a script or a unit
        # test invoking business logic directly) — g isn't bound at all.
        # Logging failure must never break the caller, same contract as the
        # DB-touching failures _write_log_row already swallows.
        return
    if not user:
        return
    _write_log_row(
        user.get('userId', ''), user.get('username', ''), user.get('role', ''),
        action, target_type, target_id, target_name, description,
        field_changes=field_changes, branch_id=branch_id,
    )


def log_api_operation(action, target_type, target_id, target_name, description):
    """Record an operation log entry for an external API Key call
    (`server/routes/open_api_*.py`, gated behind `auth.api_key_required`).

    Attributed to the key's bound user (operator_id/name/role — same
    philosophy as `auth.require_bound_key`: a key is its owner's extension),
    with `api_key_id` set to distinguish it from that user's own UI/JWT
    actions. Silently no-ops if `g.api_key_info` is missing (mirrors
    `log_operation`'s behavior when `g.current_user` is missing) or lacks the
    username/role join (pre-`api_key_required`-upgrade call sites, tests).
    """
    try:
        info = getattr(g, 'api_key_info', None)
    except RuntimeError:
        # Called outside a Flask app/request context — see log_operation.
        return
    if not info or not info.get('ownerUserId'):
        return
    username = info.get('ownerUsername')
    role = info.get('ownerRole')
    if not username or not role:
        return
    _write_log_row(
        info['ownerUserId'], username, role,
        action, target_type, target_id, target_name, description,
        api_key_id=info.get('id'),
    )
