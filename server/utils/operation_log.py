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


def log_operation(action, target_type, target_id, target_name, description):
    """
    Record an operation log entry.

    Args:
        action:       'create' | 'update' | 'delete'
        target_type:  'menu' | 'page_config' | 'dynamic_data' | 'user' | 'relation'
        target_id:    ID of the affected record (str or None)
        target_name:  Human-readable name of the target (str or None)
        description:  Chinese human-readable description
    """
    try:
        user = getattr(g, 'current_user', None)
        if not user:
            return

        log_id = f'log-{uuid.uuid4().hex[:12]}'
        operator_id = user.get('userId', '')
        operator_name = user.get('username', '')
        operator_role = user.get('role', '')

        batch_id = None
        batch_desc = None
        if has_request_context():
            batch_id = request.headers.get('X-Batch-Id') or None
            raw_desc = request.headers.get('X-Batch-Desc') or None
            batch_desc = unquote(raw_desc) if raw_desc else None

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO operation_logs '
                '(id, action, target_type, target_id, target_name, description, '
                ' operator_id, operator_name, operator_role, batch_id, batch_desc) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (log_id, action, target_type, target_id, target_name, description,
                 operator_id, operator_name, operator_role, batch_id, batch_desc),
            )
    except Exception:
        pass
