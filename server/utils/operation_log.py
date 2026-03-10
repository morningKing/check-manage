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


def log_operation(action, target_type, target_id, target_name, description, field_changes=None):
    """
    Record an operation log entry.

    Args:
        action:       'create' | 'update' | 'delete'
        target_type:  'menu' | 'page_config' | 'dynamic_data' | 'user' | 'relation'
        target_id:    ID of the affected record (str or None)
        target_name:  Human-readable name of the target (str or None)
        description:  Chinese human-readable description
        field_changes: Optional list of {field, label, from, to} dicts
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

        import psycopg2.extras
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO operation_logs '
                '(id, action, target_type, target_id, target_name, description, '
                ' operator_id, operator_name, operator_role, batch_id, batch_desc, field_changes) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (log_id, action, target_type, target_id, target_name, description,
                 operator_id, operator_name, operator_role, batch_id, batch_desc,
                 psycopg2.extras.Json(field_changes) if field_changes else None),
            )
    except Exception:
        pass
