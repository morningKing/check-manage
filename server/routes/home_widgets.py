from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required, require_permission
import psycopg2.extras
import json
import time
import uuid

home_widgets_bp = Blueprint('home_widgets', __name__)


def _row_to_json(row):
    """Convert database row to frontend JSON format (snake_case -> camelCase)."""
    return {
        'id': row['id'],
        'widgetType': row['widget_type'],
        'title': row['title'],
        'content': row['content'] if isinstance(row['content'], dict) else json.loads(row['content'] or '{}'),
        'enabled': row['enabled'],
        'order': row['order'],
        'visibleRoles': row['visible_roles'] if isinstance(row['visible_roles'], list) else json.loads(row['visible_roles'] or '[]'),
        'createdAt': row['created_at'].isoformat() if row['created_at'] else None,
        'updatedAt': row['updated_at'].isoformat() if row['updated_at'] else None,
    }


@home_widgets_bp.route('/home-widgets', methods=['GET'])
@login_required
def list_home_widgets():
    """Get home widgets list sorted by order. All roles can read."""
    user = g.current_user
    user_role = user.get('role', 'guest')

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            'SELECT id, widget_type, title, content, enabled, "order", visible_roles, created_at, updated_at '
            'FROM home_widgets WHERE enabled = TRUE ORDER BY "order"'
        )
        rows = cur.fetchall()

    # Filter by visible_roles
    widgets = []
    for row in rows:
        visible_roles = row['visible_roles'] if isinstance(row['visible_roles'], list) else json.loads(row['visible_roles'] or '[]')
        if user_role in visible_roles:
            widgets.append(_row_to_json(row))

    return jsonify(widgets)


@home_widgets_bp.route('/home-widgets', methods=['PUT'])
@require_permission('admin.home_widgets')
def batch_update_home_widgets():
    """Batch update widget configs. Admin only."""
    body = request.get_json(force=True)
    widgets = body.get('widgets', [])

    if not widgets:
        return jsonify({"error": "widgets array is required"}), 400

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        for widget in widgets:
            widget_id = widget.get('id')
            if not widget_id:
                continue

            sets = []
            params = []

            if 'title' in widget:
                sets.append('title = %s')
                params.append(widget['title'])
            if 'content' in widget:
                sets.append('content = %s')
                params.append(psycopg2.extras.Json(widget['content']))
            if 'enabled' in widget:
                sets.append('enabled = %s')
                params.append(widget['enabled'])
            if 'visibleRoles' in widget:
                sets.append('visible_roles = %s')
                params.append(psycopg2.extras.Json(widget['visibleRoles']))

            if sets:
                sets.append('updated_at = NOW()')
                params.append(widget_id)
                cur.execute(
                    f'UPDATE home_widgets SET {", ".join(sets)} WHERE id = %s',
                    params
                )

        # Return updated list
        cur.execute(
            'SELECT id, widget_type, title, content, enabled, "order", visible_roles, created_at, updated_at '
            'FROM home_widgets ORDER BY "order"'
        )
        rows = cur.fetchall()
        conn.commit()

    return jsonify([_row_to_json(row) for row in rows])


@home_widgets_bp.route('/home-widgets', methods=['POST'])
@require_permission('admin.home_widgets')
def create_home_widget():
    """Create a custom widget. Admin only."""
    body = request.get_json(force=True)
    widget_type = body.get('widgetType', '')

    if widget_type not in ('custom-markdown', 'data-card', 'quick-form'):
        return jsonify({"error": "Only custom-markdown, data-card or quick-form types are allowed"}), 400

    # Generate ID: custom-{type}-{uuid8}
    widget_id = f'custom-{widget_type}-{uuid.uuid4().hex[:8]}'

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get max order
        cur.execute('SELECT COALESCE(MAX("order"), 0) + 1 AS max_order FROM home_widgets')
        max_order_row = cur.fetchone()
        new_order = max_order_row['max_order']

        # Insert new widget
        cur.execute(
            'INSERT INTO home_widgets (id, widget_type, title, content, enabled, "order", visible_roles) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s) '
            'RETURNING id, widget_type, title, content, enabled, "order", visible_roles, created_at, updated_at',
            (
                widget_id,
                widget_type,
                body.get('title', ''),
                psycopg2.extras.Json(body.get('content', {})),
                body.get('enabled', True),
                new_order,
                psycopg2.extras.Json(body.get('visibleRoles', ['admin', 'developer', 'guest']))
            )
        )
        new_row = cur.fetchone()
        conn.commit()

    return jsonify(_row_to_json(new_row)), 201


@home_widgets_bp.route('/home-widgets/<widget_id>', methods=['DELETE'])
@require_permission('admin.home_widgets')
def delete_home_widget(widget_id):
    """Delete a custom widget. Admin only. Only allows deleting custom-* widgets."""
    # Only allow deleting custom-* widgets
    if not widget_id.startswith('custom-'):
        return jsonify({"error": "Only custom widgets can be deleted"}), 400

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM home_widgets WHERE id = %s', (widget_id,))

        if cur.rowcount == 0:
            return jsonify({"error": "Widget not found"}), 404
        conn.commit()

    return jsonify({"success": True})


@home_widgets_bp.route('/home-widgets/order', methods=['PUT'])
@require_permission('admin.home_widgets')
def update_home_widgets_order():
    """Update widgets order. Admin only."""
    body = request.get_json(force=True)
    orders = body.get('orders', [])

    if not orders:
        return jsonify({"error": "orders array is required"}), 400

    with get_db() as conn:
        cur = conn.cursor()

        for item in orders:
            widget_id = item.get('id')
            new_order = item.get('order')
            if widget_id and new_order is not None:
                cur.execute(
                    'UPDATE home_widgets SET "order" = %s, updated_at = NOW() WHERE id = %s',
                    (new_order, widget_id)
                )
        conn.commit()

    return jsonify({"success": True})