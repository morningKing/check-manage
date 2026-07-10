from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required, require_permission
from utils.permissions import can_admin
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
        'layout': {
            'x': row.get('layout_x', 0),
            'y': row.get('layout_y', 0),
            'w': row.get('layout_w', 12),
            'h': row.get('layout_h', 4),
        },
        'createdAt': row['created_at'].isoformat() if row['created_at'] else None,
        'updatedAt': row['updated_at'].isoformat() if row['updated_at'] else None,
    }


@home_widgets_bp.route('/home-widgets', methods=['GET'])
@login_required
def list_home_widgets():
    """Get home widgets list sorted by order.

    Default (home page): only enabled widgets visible to the current role.
    ``?all=true`` (admin config page): every widget unfiltered, so a disabled
    widget can still be re-enabled. Gated by the ``admin.home_widgets`` capability
    — non-admins always get the filtered list regardless of the param.
    """
    user = g.current_user
    user_role = user.get('role', 'guest')
    want_all = request.args.get('all', 'false').lower() == 'true'
    admin_all = want_all and can_admin(user_role, 'admin.home_widgets')

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if admin_all:
            cur.execute(
                'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
                'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
                'FROM home_widgets ORDER BY "order"'
            )
        else:
            cur.execute(
                'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
                'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
                'FROM home_widgets WHERE enabled = TRUE ORDER BY "order"'
            )
        rows = cur.fetchall()

    if admin_all:
        return jsonify([_row_to_json(row) for row in rows])

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
            'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
            'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
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

    allowed_types = (
        'custom-markdown', 'data-card', 'quick-form',
        'chart', 'todo', 'activity', 'announcement',
    )
    if widget_type not in allowed_types:
        return jsonify({"error": f"Widget type must be one of: {', '.join(allowed_types)}"}), 400

    # Generate ID: custom-{type}-{uuid8}
    widget_id = f'custom-{widget_type}-{uuid.uuid4().hex[:8]}'

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get max order
        cur.execute('SELECT COALESCE(MAX("order"), 0) + 1 AS max_order FROM home_widgets')
        max_order_row = cur.fetchone()
        new_order = max_order_row['max_order']

        # New widget is appended to the bottom of the grid, full width
        cur.execute('SELECT COALESCE(MAX(layout_y + layout_h), 0) AS max_bottom FROM home_widgets')
        max_bottom_row = cur.fetchone()
        new_y = max_bottom_row['max_bottom']

        # Insert new widget
        cur.execute(
            'INSERT INTO home_widgets (id, widget_type, title, content, enabled, "order", visible_roles, '
            'layout_x, layout_y, layout_w, layout_h) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) '
            'RETURNING id, widget_type, title, content, enabled, "order", visible_roles, '
            'layout_x, layout_y, layout_w, layout_h, created_at, updated_at',
            (
                widget_id,
                widget_type,
                body.get('title', ''),
                psycopg2.extras.Json(body.get('content', {})),
                body.get('enabled', True),
                new_order,
                psycopg2.extras.Json(body.get('visibleRoles', ['admin', 'developer', 'guest'])),
                0, new_y, 12, 4,
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


@home_widgets_bp.route('/home-widgets/layout', methods=['PUT'])
@require_permission('admin.home_widgets')
def update_home_widgets_layout():
    """Batch update widget grid positions (x/y/w/h). Admin only.

    Recomputes and persists `order` from the new (y, x) reading order in the
    same transaction, so mobile rendering (which still sorts by `order`)
    stays in sync with the grid without any extra writes elsewhere.
    """
    body = request.get_json(force=True)
    layout = body.get('layout', [])

    if not layout:
        return jsonify({"error": "layout array is required"}), 400

    for item in layout:
        widget_id = item.get('id')
        x, y, w, h = item.get('x'), item.get('y'), item.get('w'), item.get('h')
        if not widget_id:
            return jsonify({"error": "each layout item requires an id"}), 400
        if not isinstance(x, int) or not isinstance(y, int) or not isinstance(w, int) or not isinstance(h, int):
            return jsonify({"error": "x/y/w/h must be integers"}), 400
        if not (0 <= x <= 11):
            return jsonify({"error": f"x must be within 0-11, got {x}"}), 400
        if not (1 <= w <= 12) or x + w > 12:
            return jsonify({"error": f"w must be within 1-12 and x+w<=12, got x={x} w={w}"}), 400
        if y < 0:
            return jsonify({"error": f"y must be >= 0, got {y}"}), 400
        if h < 1:
            return jsonify({"error": f"h must be >= 1, got {h}"}), 400

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        for item in layout:
            cur.execute(
                'UPDATE home_widgets SET layout_x = %s, layout_y = %s, layout_w = %s, layout_h = %s, updated_at = NOW() '
                'WHERE id = %s',
                (item['x'], item['y'], item['w'], item['h'], item['id'])
            )

        # Recompute `order` from the new reading order (y, x)
        cur.execute('SELECT id FROM home_widgets ORDER BY layout_y, layout_x')
        ids_in_order = [row['id'] for row in cur.fetchall()]
        for idx, widget_id in enumerate(ids_in_order):
            cur.execute('UPDATE home_widgets SET "order" = %s WHERE id = %s', (idx + 1, widget_id))

        cur.execute(
            'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
            'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
            'FROM home_widgets ORDER BY "order"'
        )
        rows = cur.fetchall()
        conn.commit()

    return jsonify([_row_to_json(row) for row in rows])