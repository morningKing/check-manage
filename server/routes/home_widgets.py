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


def _validate_layout_item(x, y, w, h):
    """Validate a single grid position. Returns an error message string, or None if valid."""
    if not isinstance(x, int) or not isinstance(y, int) or not isinstance(w, int) or not isinstance(h, int):
        return "x/y/w/h must be integers"
    if not (0 <= x <= 11):
        return f"x must be within 0-11, got {x}"
    if not (1 <= w <= 12) or x + w > 12:
        return f"w must be within 1-12 and x+w<=12, got x={x} w={w}"
    if y < 0:
        return f"y must be >= 0, got {y}"
    if h < 1:
        return f"h must be >= 1, got {h}"
    return None


def _recompute_order(cur):
    """Recompute and persist `order` from the (layout_y, layout_x) reading order.

    Must run inside the caller's existing transaction (does not commit).
    """
    cur.execute('SELECT id FROM home_widgets ORDER BY layout_y, layout_x')
    ids_in_order = [row['id'] for row in cur.fetchall()]
    for idx, widget_id in enumerate(ids_in_order):
        cur.execute('UPDATE home_widgets SET "order" = %s WHERE id = %s', (idx + 1, widget_id))


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
    """Create a custom widget. Admin only.

    Optional body field `layout: {x,y,w,h}` places the widget at an explicit
    grid position (used by drag-to-create from the block palette). When
    omitted, the widget is appended full-width to the bottom of the grid
    (unchanged default behavior for the "click to add" path).
    """
    body = request.get_json(force=True)
    widget_type = body.get('widgetType', '')

    allowed_types = (
        'custom-markdown', 'data-card', 'quick-form',
        'chart', 'todo', 'activity', 'announcement',
    )
    if widget_type not in allowed_types:
        return jsonify({"error": f"Widget type must be one of: {', '.join(allowed_types)}"}), 400

    layout = body.get('layout')
    if layout is not None:
        if not isinstance(layout, dict):
            return jsonify({"error": "layout must be an object"}), 400
        error = _validate_layout_item(layout.get('x'), layout.get('y'), layout.get('w'), layout.get('h'))
        if error:
            return jsonify({"error": error}), 400

    # Generate ID: custom-{type}-{uuid8}
    widget_id = f'custom-{widget_type}-{uuid.uuid4().hex[:8]}'

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get max order
        cur.execute('SELECT COALESCE(MAX("order"), 0) + 1 AS max_order FROM home_widgets')
        max_order_row = cur.fetchone()
        new_order = max_order_row['max_order']

        if layout is not None:
            new_x, new_y, new_w, new_h = layout['x'], layout['y'], layout['w'], layout['h']
        else:
            # New widget is appended to the bottom of the grid, full width
            cur.execute('SELECT COALESCE(MAX(layout_y + layout_h), 0) AS max_bottom FROM home_widgets')
            max_bottom_row = cur.fetchone()
            new_x, new_y, new_w, new_h = 0, max_bottom_row['max_bottom'], 12, 4

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
                new_x, new_y, new_w, new_h,
            )
        )
        new_row = cur.fetchone()

        if layout is not None:
            # Explicit position was placed somewhere other than "the bottom" —
            # recompute order from (y, x) so mobile stacking matches the grid.
            _recompute_order(cur)
            cur.execute(
                'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
                'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
                'FROM home_widgets WHERE id = %s',
                (widget_id,)
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
        if not widget_id:
            return jsonify({"error": "each layout item requires an id"}), 400
        error = _validate_layout_item(item.get('x'), item.get('y'), item.get('w'), item.get('h'))
        if error:
            return jsonify({"error": error}), 400

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        for item in layout:
            cur.execute(
                'UPDATE home_widgets SET layout_x = %s, layout_y = %s, layout_w = %s, layout_h = %s, updated_at = NOW() '
                'WHERE id = %s',
                (item['x'], item['y'], item['w'], item['h'], item['id'])
            )

        _recompute_order(cur)

        cur.execute(
            'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
            'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
            'FROM home_widgets ORDER BY "order"'
        )
        rows = cur.fetchall()
        conn.commit()

    return jsonify([_row_to_json(row) for row in rows])