from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required, admin_required
import psycopg2.extras
import uuid

dashboards_bp = Blueprint('dashboards', __name__)


def row_to_dict(row):
    return {
        'id': row[0],
        'name': row[1],
        'description': row[2],
        'layout': row[3] or [],
        'ownerId': row[4],
        'isGlobal': row[5],
        'createdAt': row[6].isoformat() if row[6] else None,
        'updatedAt': row[7].isoformat() if row[7] else None,
    }


@dashboards_bp.route('/dashboards', methods=['GET'])
@login_required
def list_dashboards():
    user = g.current_user
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, description, layout, owner_id, is_global, created_at, updated_at '
            'FROM dashboards WHERE is_global = TRUE OR owner_id = %s ORDER BY created_at',
            (user.get('userId', ''),)
        )
        rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@dashboards_bp.route('/dashboards/<dash_id>', methods=['GET'])
@login_required
def get_dashboard(dash_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, description, layout, owner_id, is_global, created_at, updated_at '
            'FROM dashboards WHERE id = %s', (dash_id,)
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row_to_dict(row))


@dashboards_bp.route('/dashboards', methods=['POST'])
@admin_required
def create_dashboard():
    body = request.get_json(force=True)
    user = g.current_user
    dash_id = body.get('id') or f'dash-{uuid.uuid4().hex[:12]}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dashboards (id, name, description, layout, owner_id, is_global) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (dash_id, body.get('name', ''), body.get('description', ''),
             psycopg2.extras.Json(body.get('layout', [])),
             user.get('userId', ''), body.get('isGlobal', False))
        )
    body['id'] = dash_id
    return jsonify(body), 201


@dashboards_bp.route('/dashboards/<dash_id>', methods=['PUT'])
@admin_required
def update_dashboard(dash_id):
    body = request.get_json(force=True)
    sets, params = [], []
    if 'name' in body:
        sets.append('name=%s'); params.append(body['name'])
    if 'description' in body:
        sets.append('description=%s'); params.append(body['description'])
    if 'layout' in body:
        sets.append('layout=%s'); params.append(psycopg2.extras.Json(body['layout']))
    if 'isGlobal' in body:
        sets.append('is_global=%s'); params.append(body['isGlobal'])
    sets.append('updated_at=NOW()')
    if sets:
        params.append(dash_id)
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(f'UPDATE dashboards SET {", ".join(sets)} WHERE id=%s', params)
    return jsonify(body)


@dashboards_bp.route('/dashboards/<dash_id>', methods=['DELETE'])
@admin_required
def delete_dashboard(dash_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dashboards WHERE id = %s', (dash_id,))
    return jsonify({})


@dashboards_bp.route('/dashboards/aggregate', methods=['POST'])
@login_required
def aggregate():
    """Run aggregation queries on dynamic_data for dashboard widgets."""
    body = request.get_json(force=True)
    collection = body.get('collection', '')
    metric = body.get('metric', 'count')
    field = body.get('field')
    group_field = body.get('groupField')
    filter_query = body.get('filter', {})

    if not collection:
        return jsonify({"error": "collection is required"}), 400

    with get_db() as conn:
        cur = conn.cursor()

        # Build WHERE clause
        where_parts = ["collection = %s"]
        params = [collection]
        for key, val in filter_query.items():
            if key.startswith('$'):
                continue  # Skip complex operators for now
            where_parts.append("data->>%s = %s")
            params.extend([key, str(val)])

        where_clause = ' AND '.join(where_parts)

        if group_field:
            # Grouped aggregation
            if metric == 'count':
                sql = f"SELECT data->>%s as group_key, COUNT(*) FROM dynamic_data WHERE {where_clause} GROUP BY data->>%s"
                params_final = [group_field] + params + [group_field]
            elif metric in ('sum', 'avg', 'min', 'max'):
                agg_fn = metric.upper()
                sql = f"SELECT data->>%s as group_key, {agg_fn}((data->>%s)::numeric) FROM dynamic_data WHERE {where_clause} GROUP BY data->>%s"
                params_final = [group_field, field] + params + [group_field]
            else:
                return jsonify({"error": f"Unsupported metric: {metric}"}), 400
            cur.execute(sql, params_final)
            rows = cur.fetchall()
            result = [{'key': r[0], 'value': float(r[1]) if r[1] is not None else 0} for r in rows]
            return jsonify({'type': 'grouped', 'data': result})
        else:
            # Single value aggregation
            if metric == 'count':
                sql = f"SELECT COUNT(*) FROM dynamic_data WHERE {where_clause}"
                params_final = params
            elif metric in ('sum', 'avg', 'min', 'max'):
                agg_fn = metric.upper()
                sql = f"SELECT {agg_fn}((data->>%s)::numeric) FROM dynamic_data WHERE {where_clause}"
                params_final = [field] + params
            else:
                return jsonify({"error": f"Unsupported metric: {metric}"}), 400
            cur.execute(sql, params_final)
            row = cur.fetchone()
            value = float(row[0]) if row and row[0] is not None else 0
            return jsonify({'type': 'single', 'value': value})
