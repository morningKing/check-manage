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


# ---------------------------------------------------------------------------
# Aggregation API
# ---------------------------------------------------------------------------

VALID_METRICS = {'count', 'sum', 'avg', 'min', 'max', 'uniqueCount'}
SORT_MAP = {
    'value_desc': 'agg_value DESC',
    'value_asc': 'agg_value ASC',
    'key_desc': 'group_key DESC',
    'key_asc': 'group_key ASC',
}
DATE_TRUNC_MAP = {
    'day': 'day',
    'week': 'week',
    'month': 'month',
    'year': 'year',
}


def _build_where(collection, filter_query, table_alias='', branch_id='main'):
    """Build WHERE clause parts and params."""
    prefix = f"{table_alias}." if table_alias else ''
    parts = [f"{prefix}collection = %s", f"{prefix}branch_id = %s"]
    params = [collection, branch_id]
    for key, val in (filter_query or {}).items():
        if key.startswith('$'):
            continue
        parts.append(f"{prefix}data->>%s = %s")
        params.extend([key, str(val)])
    return ' AND '.join(parts), params


def _load_page_fields(cur, collection):
    """Load field definitions for a collection from page_configs."""
    cur.execute(
        "SELECT fields FROM page_configs WHERE id = %s",
        (f'page-{collection}',)
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0] if isinstance(row[0], list) else []
    return []


def _resolve_target_fields(cur, target_collections):
    """Load page_configs for a set of target collections. Returns {pageId: fields}."""
    if not target_collections:
        return {}
    result = {}
    for col in target_collections:
        page_id = f'page-{col}'
        cur.execute("SELECT fields FROM page_configs WHERE id = %s", (page_id,))
        row = cur.fetchone()
        if row and row[0]:
            result[page_id] = row[0] if isinstance(row[0], list) else []
    return result


def get_user_current_branch(user_id, collection):
    """Get user's current branch for a collection."""
    return 'main'


def _metric_expr(metric_type, field):
    """Return SQL expression and extra params for a metric."""
    if metric_type == 'count':
        return 'COUNT(*)', []
    elif metric_type == 'uniqueCount':
        return 'COUNT(DISTINCT data->>%s)', [field]
    elif metric_type in ('sum', 'avg', 'min', 'max'):
        return f'{metric_type.upper()}((data->>%s)::numeric)', [field]
    return None, []


@dashboards_bp.route('/dashboards/aggregate', methods=['POST'])
@login_required
def aggregate_data():
    """Run aggregation queries on dynamic_data for dashboard widgets.

    Supports both legacy params (metric/field/groupField) and new format
    (metrics/groupBy/sort/limit).
    """
    body = request.get_json(force=True)
    collection = body.get('collection', '')
    if not collection:
        return jsonify({"error": "collection is required"}), 400

    filter_query = body.get('filter', {})

    sort_key = body.get('sort', 'value_desc')
    limit = min(int(body.get('limit', 20)), 200)
    order_clause = SORT_MAP.get(sort_key, 'agg_value DESC')

    # Resolve metric (support new and legacy format)
    metrics_raw = body.get('metrics')
    if metrics_raw and isinstance(metrics_raw, list) and len(metrics_raw) > 0:
        metric_type = metrics_raw[0].get('type', 'count')
        field = metrics_raw[0].get('field')
    else:
        metric_type = body.get('metric', 'count')
        field = body.get('field')

    if metric_type not in VALID_METRICS:
        return jsonify({"error": f"Unsupported metric: {metric_type}"}), 400

    # Resolve groupBy (support new and legacy format)
    group_by = body.get('groupBy')
    if group_by and isinstance(group_by, dict):
        group_field = group_by.get('field')
        group_type = group_by.get('type', 'terms')
        interval = group_by.get('interval', 'month')
    else:
        group_field = body.get('groupField')
        group_type = 'terms'
        interval = 'month'

    user = g.current_user
    branch_id = get_user_current_branch(user.get('userId', ''), collection)

    with get_db() as conn:
        cur = conn.cursor()

        if group_field:
            # --- Grouped aggregation ---
            metric_sql, metric_params = _metric_expr(metric_type, field)
            if metric_sql is None:
                return jsonify({"error": f"Unsupported metric: {metric_type}"}), 400

            # Check if group_field is a relation/reference/quoteSelect field
            fields = _load_page_fields(cur, collection)
            field_def = next((f for f in fields if f.get('fieldName') == group_field), None)
            control_type = field_def.get('controlType', '') if field_def else ''

            if control_type == 'relation' and field_def:
                # M:N relation — JOIN data_relations + target dynamic_data
                rel_cfg = field_def.get('relationConfig', {})
                target_col = rel_cfg.get('targetCollection', '')
                display_field = rel_cfg.get('displayField', 'id')

                where_clause, where_params = _build_where(collection, filter_query, table_alias='dd', branch_id=branch_id)

                sql = (
                    f"SELECT tgt.data->>%s AS group_key, {metric_sql} AS agg_value "
                    f"FROM dynamic_data dd "
                    f"JOIN data_relations dr ON dr.collection = %s AND dr.record_id = dd.id "
                    f"AND dr.field_name = %s AND dr.branch_id = %s "
                    f"JOIN dynamic_data tgt ON tgt.id = dr.related_id "
                    f"AND tgt.collection = %s AND tgt.branch_id = %s "
                    f"WHERE {where_clause} "
                    f"GROUP BY group_key "
                    f"ORDER BY {order_clause} "
                    f"LIMIT %s"
                )
                params = [display_field, collection, group_field, branch_id, target_col, branch_id] + metric_params + where_params + [limit]

            elif control_type == 'reference' and field_def:
                # 1:N reference — LEFT JOIN parent record
                ref_cfg = field_def.get('referenceConfig', {})
                target_col = ref_cfg.get('targetCollection', '')
                display_field = ref_cfg.get('displayField', 'id')

                where_clause, where_params = _build_where(collection, filter_query, table_alias='dd', branch_id=branch_id)

                sql = (
                    f"SELECT tgt.data->>%s AS group_key, {metric_sql} AS agg_value "
                    f"FROM dynamic_data dd "
                    f"LEFT JOIN dynamic_data tgt ON tgt.id = dd.data->>%s "
                    f"AND tgt.collection = %s AND tgt.branch_id = %s "
                    f"WHERE {where_clause} "
                    f"GROUP BY group_key "
                    f"ORDER BY {order_clause} "
                    f"LIMIT %s"
                )
                params = [display_field, group_field, target_col, branch_id] + metric_params + where_params + [limit]

            elif control_type == 'quoteSelect' and field_def:
                # Single-direction multi-select — CROSS JOIN unnested array
                quote_cfg = field_def.get('quoteConfig', {})
                target_col = quote_cfg.get('targetCollection', '')
                display_field = quote_cfg.get('displayField', 'id')

                where_clause, where_params = _build_where(collection, filter_query, table_alias='dd', branch_id=branch_id)

                sql = (
                    f"SELECT tgt.data->>%s AS group_key, {metric_sql} AS agg_value "
                    f"FROM dynamic_data dd "
                    f"CROSS JOIN jsonb_array_elements_text(dd.data->%s) AS elem_id "
                    f"JOIN dynamic_data tgt ON tgt.id = elem_id "
                    f"AND tgt.collection = %s AND tgt.branch_id = %s "
                    f"WHERE {where_clause} "
                    f"GROUP BY group_key "
                    f"ORDER BY {order_clause} "
                    f"LIMIT %s"
                )
                params = [display_field, group_field, target_col, branch_id] + metric_params + where_params + [limit]

            else:
                # Regular field grouping
                where_clause, where_params = _build_where(collection, filter_query, branch_id=branch_id)

                if group_type == 'dateHistogram':
                    trunc = DATE_TRUNC_MAP.get(interval, 'month')
                    group_expr = f"date_trunc('{trunc}', (data->>%s)::timestamptz)"
                    group_params = [group_field]
                else:
                    group_expr = "data->>%s"
                    group_params = [group_field]

                sql = (
                    f"SELECT {group_expr} AS group_key, {metric_sql} AS agg_value "
                    f"FROM dynamic_data WHERE {where_clause} "
                    f"GROUP BY group_key "
                    f"ORDER BY {order_clause} "
                    f"LIMIT %s"
                )
                params = group_params + metric_params + where_params + [limit]

            cur.execute(sql, params)
            rows = cur.fetchall()

            data = []
            for r in rows:
                key = r[0]
                if key is None:
                    key = '(空)'
                elif hasattr(key, 'isoformat'):
                    key = key.strftime('%Y-%m-%d') if interval == 'day' else key.strftime('%Y-%m')
                else:
                    key = str(key)
                data.append({'key': key, 'value': float(r[1]) if r[1] is not None else 0})

            return jsonify({'type': 'grouped', 'data': data})
        else:
            # --- Single-value aggregation ---
            metric_sql, metric_params = _metric_expr(metric_type, field)
            if metric_sql is None:
                return jsonify({"error": f"Unsupported metric: {metric_type}"}), 400

            where_clause, where_params = _build_where(collection, filter_query, branch_id=branch_id)
            sql = f"SELECT {metric_sql} FROM dynamic_data WHERE {where_clause}"
            params = metric_params + where_params
            cur.execute(sql, params)
            row = cur.fetchone()
            value = float(row[0]) if row and row[0] is not None else 0
            return jsonify({'type': 'single', 'value': value})
