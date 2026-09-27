from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required, write_required, require_permission
from datetime import datetime, timezone
import psycopg2.extras

column_views_bp = Blueprint('column_views', __name__, url_prefix='/column-views')


def format_ts(dt):
    """Format datetime to ISO 8601 with trailing Z (UTC)."""
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def row_to_dict(row):
    return {
        'id': row[0],
        'pageId': row[1],
        'name': row[2],
        'isPublic': row[3],
        'creatorId': row[4],
        'isDefault': row[5],
        'columns': row[6],
        'sortConfig': row[7],
        'filterConfig': row[8],
        'groupConfig': row[9],
        'createdAt': format_ts(row[10]),
        'updatedAt': format_ts(row[11]),
    }


SELECT_COLUMNS = 'id, page_id, name, is_public, creator_id, is_default, columns, sort_config, filter_config, group_config, created_at, updated_at'


@column_views_bp.route('/<page_id>/views', methods=['GET'])
@login_required
def list_views(page_id):
    """Get all views: public views + user's private views."""
    user_id = g.current_user['userId']

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f'SELECT {SELECT_COLUMNS} FROM column_views '
            'WHERE page_id = %s AND (is_public = true OR creator_id = %s) '
            'ORDER BY is_public DESC, created_at ASC',
            (page_id, user_id),
        )
        rows = cur.fetchall()
        views = [row_to_dict(r) for r in rows]

        # Find default view id
        cur.execute(
            'SELECT id FROM column_views WHERE page_id = %s AND is_default = true',
            (page_id,),
        )
        default_row = cur.fetchone()
        default_view_id = default_row[0] if default_row else None

    return jsonify({'views': views, 'defaultViewId': default_view_id})


@column_views_bp.route('/<page_id>/views', methods=['POST'])
@write_required
def create_view(page_id):
    """Create a new view. Public views require admin; private views require write access."""
    body = request.get_json(force=True)
    name = body.get('name', '').strip()
    is_public = body.get('isPublic', False)

    if not name:
        return jsonify({'error': '视图名称不能为空'}), 400

    user_id = g.current_user['userId']
    user_role = g.current_user['role']

    # Public views can only be created by admin
    if is_public and user_role != 'admin':
        return jsonify({'error': '只有管理员可以创建公开视图'}), 403

    columns = body.get('columns', [])
    sort_config = body.get('sortConfig', [])
    filter_config = body.get('filterConfig', [])
    group_config = body.get('groupConfig', None)

    with get_db() as conn:
        cur = conn.cursor()

        # Validate page exists
        cur.execute('SELECT id FROM page_configs WHERE id = %s', (page_id,))
        if not cur.fetchone():
            return jsonify({'error': '页面配置不存在'}), 404

        # Check name uniqueness: public views must be unique within page_id;
        # private views must be unique within page_id + creator_id
        if is_public:
            cur.execute(
                'SELECT id FROM column_views WHERE page_id = %s AND is_public = true AND name = %s',
                (page_id, name),
            )
        else:
            cur.execute(
                'SELECT id FROM column_views WHERE page_id = %s AND is_public = false AND creator_id = %s AND name = %s',
                (page_id, user_id, name),
            )
        if cur.fetchone():
            return jsonify({'error': '同名视图已存在'}), 400

        now = datetime.now(timezone.utc)
        cur.execute(
            f'INSERT INTO column_views (page_id, name, is_public, creator_id, is_default, columns, sort_config, filter_config, group_config, created_at, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) '
            f'RETURNING {SELECT_COLUMNS}',
            (page_id, name, is_public, user_id, False,
             psycopg2.extras.Json(columns),
             psycopg2.extras.Json(sort_config),
             psycopg2.extras.Json(filter_config),
             psycopg2.extras.Json(group_config),
             now, now),
        )
        row = cur.fetchone()

    return jsonify(row_to_dict(row)), 201


@column_views_bp.route('/<page_id>/views/<int:view_id>', methods=['PUT'])
@login_required
def update_view(page_id, view_id):
    """Update a view. Only creator or admin can update."""
    body = request.get_json(force=True)
    user_id = g.current_user['userId']
    user_role = g.current_user['role']

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f'SELECT {SELECT_COLUMNS} FROM column_views WHERE id = %s AND page_id = %s',
            (view_id, page_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '视图不存在'}), 404

        view = row_to_dict(row)

        # Permission check: creator or admin
        if view['creatorId'] != user_id and user_role != 'admin':
            return jsonify({'error': '无权修改此视图'}), 403

        # Build update
        sets = []
        params = []

        if 'name' in body:
            new_name = body['name'].strip()
            if not new_name:
                return jsonify({'error': '视图名称不能为空'}), 400
            # Check uniqueness
            if view['isPublic']:
                cur.execute(
                    'SELECT id FROM column_views WHERE page_id = %s AND is_public = true AND name = %s AND id != %s',
                    (page_id, new_name, view_id),
                )
            else:
                cur.execute(
                    'SELECT id FROM column_views WHERE page_id = %s AND is_public = false AND creator_id = %s AND name = %s AND id != %s',
                    (page_id, view['creatorId'], new_name, view_id),
                )
            if cur.fetchone():
                return jsonify({'error': '同名视图已存在'}), 400
            sets.append('name=%s')
            params.append(new_name)

        # Changing is_public: only admin can make private -> public
        if 'isPublic' in body:
            if body['isPublic'] and user_role != 'admin':
                return jsonify({'error': '只有管理员可以将视图设为公开'}), 403
            sets.append('is_public=%s')
            params.append(body['isPublic'])

        if 'columns' in body:
            sets.append('columns=%s')
            params.append(psycopg2.extras.Json(body['columns']))
        if 'sortConfig' in body:
            sets.append('sort_config=%s')
            params.append(psycopg2.extras.Json(body['sortConfig']))
        if 'filterConfig' in body:
            sets.append('filter_config=%s')
            params.append(psycopg2.extras.Json(body['filterConfig']))
        if 'groupConfig' in body:
            sets.append('group_config=%s')
            params.append(psycopg2.extras.Json(body['groupConfig']))

        if sets:
            sets.append('updated_at=%s')
            params.append(datetime.now(timezone.utc))
            params.append(view_id)
            cur.execute(
                f'UPDATE column_views SET {", ".join(sets)} WHERE id=%s',
                params,
            )

        # Return updated record
        cur.execute(f'SELECT {SELECT_COLUMNS} FROM column_views WHERE id = %s', (view_id,))
        row = cur.fetchone()

    return jsonify(row_to_dict(row))


@column_views_bp.route('/<page_id>/views/<int:view_id>', methods=['DELETE'])
@login_required
def delete_view(page_id, view_id):
    """Delete a view. Cannot delete default views. Only creator or admin."""
    user_id = g.current_user['userId']
    user_role = g.current_user['role']

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f'SELECT {SELECT_COLUMNS} FROM column_views WHERE id = %s AND page_id = %s',
            (view_id, page_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '视图不存在'}), 404

        view = row_to_dict(row)

        if view['isDefault']:
            return jsonify({'error': '不能删除默认视图'}), 400

        # Permission check: creator or admin
        if view['creatorId'] != user_id and user_role != 'admin':
            return jsonify({'error': '无权删除此视图'}), 403

        cur.execute('DELETE FROM column_views WHERE id = %s', (view_id,))

    return jsonify({})


@column_views_bp.route('/<page_id>/views/<int:view_id>/default', methods=['PUT'])
@require_permission('admin.column_views')
def set_default(page_id, view_id):
    """Set a public view as the page default. Admin only."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f'SELECT {SELECT_COLUMNS} FROM column_views WHERE id = %s AND page_id = %s',
            (view_id, page_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '视图不存在'}), 404

        view = row_to_dict(row)
        if not view['isPublic']:
            return jsonify({'error': '只有公开视图可以设为默认'}), 400

        # Clear existing default for this page (unique index enforces one default per page)
        cur.execute(
            'UPDATE column_views SET is_default = false WHERE page_id = %s AND is_default = true',
            (page_id,),
        )
        cur.execute(
            'UPDATE column_views SET is_default = true WHERE id = %s',
            (view_id,),
        )

    return jsonify({'message': '已设为默认视图'})


@column_views_bp.route('/<page_id>/views/<int:view_id>/copy', methods=['POST'])
@login_required
def copy_view(page_id, view_id):
    """Copy a view. Creates a new private view with the same config."""
    user_id = g.current_user['userId']

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f'SELECT {SELECT_COLUMNS} FROM column_views WHERE id = %s AND page_id = %s',
            (view_id, page_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '视图不存在'}), 404

        source = row_to_dict(row)

        # Generate unique name: append " - 副本", then number suffix if needed
        base_name = source['name'] + ' - 副本'
        copy_name = base_name
        suffix = 2
        while True:
            cur.execute(
                'SELECT id FROM column_views WHERE page_id = %s AND is_public = false AND creator_id = %s AND name = %s',
                (page_id, user_id, copy_name),
            )
            if not cur.fetchone():
                break
            copy_name = f'{base_name} ({suffix})'
            suffix += 1

        now = datetime.now(timezone.utc)
        cur.execute(
            f'INSERT INTO column_views (page_id, name, is_public, creator_id, is_default, columns, sort_config, filter_config, group_config, created_at, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) '
            f'RETURNING {SELECT_COLUMNS}',
            (page_id, copy_name, False, user_id, False,
             psycopg2.extras.Json(source['columns']),
             psycopg2.extras.Json(source['sortConfig']),
             psycopg2.extras.Json(source['filterConfig']),
             psycopg2.extras.Json(source['groupConfig']),
             now, now),
        )
        row = cur.fetchone()

    return jsonify(row_to_dict(row)), 201
