"""
Notification helper.

Creates notification records. All exceptions caught internally
so notification failure never breaks primary operations.
"""
import uuid
from db import get_db
import psycopg2.extras


def create_notification(user_id, ntype, title, content=None, source_collection=None, source_record_id=None):
    """Create a single notification record."""
    try:
        notif_id = f'notif-{uuid.uuid4().hex[:12]}'
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO notifications (id, user_id, type, title, content, source_collection, source_record_id) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                (notif_id, user_id, ntype, title, content, source_collection, source_record_id)
            )
    except Exception:
        pass


def notify_status_change(collection, record_id, field_label, old_status, new_status, operator_name, record_data=None):
    """Notify relevant users about a status change."""
    try:
        title = f'{field_label}变更：{old_status} → {new_status}'
        content = f'{operator_name} 将 {field_label} 从「{old_status}」改为「{new_status}」'
        # Notify the creator/assignee if present in record_data
        if record_data:
            for field_name in ('createdBy', 'assignee', 'owner', 'creator'):
                user_id = record_data.get(field_name)
                if user_id and isinstance(user_id, str):
                    create_notification(user_id, 'statusChange', title, content, collection, record_id)
    except Exception:
        pass


def notify_mention(collection, record_id, mentioned_user_ids, author_name):
    """Send @mention notifications."""
    try:
        title = f'{author_name} 在评论中提及了您'
        for user_id in mentioned_user_ids:
            create_notification(user_id, 'mention', title, None, collection, record_id)
    except Exception:
        pass


def notify_dependency_broken(source_project_id, source_project_name, target_project_name, reason):
    """Notify source project admins that their dependency is broken."""
    try:
        title = f'依赖断裂：{target_project_name}'
        content = f'项目「{source_project_name}」对「{target_project_name}」的依赖已断裂。原因：{reason}'
        # Get project admins
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id FROM users WHERE role = %s',
                ('admin',)
            )
            admin_rows = cur.fetchall()
            for row in admin_rows:
                create_notification(row[0], 'dependencyBroken', title, content, source_project_id, None)
    except Exception:
        pass


def notify_dependency_warning(source_project_id, source_project_name, target_project_name, detail):
    """Notify source project admins about dependency warnings (e.g., broken foreign keys)."""
    try:
        title = f'依赖警告：{target_project_name}'
        content = f'项目「{source_project_name}」对「{target_project_name}」的依赖存在警告。详情：{detail}'
        # Get project admins
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id FROM users WHERE role = %s',
                ('admin',)
            )
            admin_rows = cur.fetchall()
            for row in admin_rows:
                create_notification(row[0], 'dependencyWarning', title, content, source_project_id, None)
    except Exception:
        pass


def notify_dependency_resolved(source_project_id, source_project_name, target_project_name):
    """Notify source project admins that their dependency is now valid."""
    try:
        title = f'依赖恢复正常：{target_project_name}'
        content = f'项目「{source_project_name}」对「{target_project_name}」的依赖已恢复正常'
        # Get project admins
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id FROM users WHERE role = %s',
                ('admin',)
            )
            admin_rows = cur.fetchall()
            for row in admin_rows:
                create_notification(row[0], 'dependencyResolved', title, content, source_project_id, None)
    except Exception:
        pass
