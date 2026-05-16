from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required, admin_required
import psycopg2.extras
import uuid
from utils.version import get_user_current_branch as resolve_user_current_branch

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

VALID_METRICS = {'count', 'sum', 'avg', 'min', 'max', 'uniqueCount', 'arrayLengthSum', 'arrayLengthAvg', 'arrayLengthMax', 'arrayLengthMin', 'relationCountSum', 'relationCountAvg', 'relationCountMax', 'relationCountMin'}
SORT_MAP = {
    'value_desc': '{metric_alias} DESC',
    'value_asc': '{metric_alias} ASC',
    'key_desc': 'group_key DESC',
    'key_asc': 'group_key ASC',
}
DATE_TRUNC_MAP = {
    'day': 'day',
    'week': 'week',
    'month': 'month',
    'year': 'year',
}
VALID_GROUP_TYPES = {'terms', 'dateHistogram', 'histogram', 'range', 'exists'}


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
    return resolve_user_current_branch(user_id, collection)


def _metric_expr(metric_type, field, data_expr='data', is_relation=False, collection=None, branch_id='main', table_alias='dd'):
    """Return SQL expression and extra params for a metric.

    For relation fields, use subquery to count relations via data_relations table.
    """
    if metric_type == 'count':
        return 'COUNT(*)', []
    elif metric_type == 'uniqueCount':
        return f'COUNT(DISTINCT {data_expr}->>%s)', [field]
    elif metric_type in ('sum', 'avg', 'min', 'max'):
        return f'{metric_type.upper()}(({data_expr}->>%s)::numeric)', [field]
    elif metric_type in ('arrayLengthSum', 'arrayLengthAvg', 'arrayLengthMax', 'arrayLengthMin'):
        agg_func = metric_type.replace('arrayLength', '').upper()
        return f'{agg_func}(COALESCE(jsonb_array_length({data_expr}->%s), 0))', [field]
    elif metric_type in ('relationCountSum', 'relationCountAvg', 'relationCountMax', 'relationCountMin'):
        # For relation fields, aggregate the count of relations per record
        agg_func = metric_type.replace('relationCount', '').upper()
        prefix = f"{table_alias}." if table_alias else ''
        subquery = (
            f"(SELECT COUNT(*) FROM data_relations dr "
            f"WHERE dr.collection = %s AND dr.record_id = {prefix}id "
            f"AND dr.field_name = %s AND dr.branch_id = %s)"
        )
        return f'{agg_func}({subquery})', [collection, field, branch_id]
    return None, []


def _normalize_metrics(body):
    """Normalize metrics in request order."""
    metrics_raw = body.get('metrics')
    if metrics_raw and isinstance(metrics_raw, list):
        metrics = metrics_raw
    else:
        metrics = [{
            'type': body.get('metric', 'count'),
            'field': body.get('field'),
        }]

    normalized = []
    for idx, metric in enumerate(metrics):
        metric_type = metric.get('type', 'count')
        field = metric.get('field')
        if metric_type not in VALID_METRICS:
            raise ValueError(f'Unsupported metric: {metric_type}')
        if metric_type != 'count' and not field:
            raise ValueError(f'Metric field is required for {metric_type}')

        metric_name = metric.get('name')
        if not metric_name:
            metric_name = metric_type if metric_type == 'count' else f'{metric_type}_{field}'

        normalized.append({
            'type': metric_type,
            'field': field,
            'name': metric_name,
            'alias': 'agg_value' if idx == 0 else f'metric_{idx}',
        })
    return normalized


def _normalize_group_by(body):
    """Normalize groupBy configuration from request body."""
    group_by = body.get('groupBy')
    if group_by and isinstance(group_by, dict):
        group_field = group_by.get('field')
        group_type = group_by.get('type', 'terms')
        interval = group_by.get('interval', 'month')
        ranges = group_by.get('ranges', [])
        offset = group_by.get('offset', 0)
    else:
        group_field = body.get('groupField')
        group_type = 'terms'
        interval = 'month'
        ranges = []
        offset = 0

    if group_field and group_type not in VALID_GROUP_TYPES:
        raise ValueError(f'Unsupported group type: {group_type}')

    return {
        'field': group_field,
        'type': group_type,
        'interval': interval,
        'ranges': ranges,
        'offset': offset,
    }


def _normalize_optional_group_config(config):
    """Normalize an optional secondary grouping config."""
    if not config:
        return None
    if not isinstance(config, dict):
        raise ValueError('breakdownBy must be an object')

    field = config.get('field')
    group_type = config.get('type', 'terms')
    if not field:
        raise ValueError('breakdownBy.field is required')
    if group_type not in VALID_GROUP_TYPES:
        raise ValueError(f'Unsupported group type: {group_type}')

    return {
        'field': field,
        'type': group_type,
        'interval': config.get('interval', 'month'),
        'ranges': config.get('ranges', []),
        'offset': config.get('offset', 0),
    }


def _build_metric_selects(metrics, data_expr='data', collection=None, branch_id='main', table_alias='dd', fields=None):
    """Build metric select fragments and params.

    Supports relation field aggregation via subquery on data_relations.
    """
    select_parts = []
    params = []
    for metric in metrics:
        metric_type = metric['type']
        field = metric.get('field')

        # Check if field is relation type for arrayLength aggregations
        is_relation = False
        if fields and field and metric_type.startswith('arrayLength'):
            field_def = next((f for f in fields if f.get('fieldName') == field), None)
            if field_def and field_def.get('controlType') == 'relation':
                is_relation = True
                # Convert arrayLength to relationCount for relation fields
                agg_func = metric_type.replace('arrayLength', '').upper()
                prefix = f"{table_alias}." if table_alias else ''
                subquery = (
                    f"(SELECT COUNT(*) FROM data_relations dr "
                    f"WHERE dr.collection = %s AND dr.record_id = {prefix}id "
                    f"AND dr.field_name = %s AND dr.branch_id = %s)"
                )
                expr = f'{agg_func}({subquery})'
                expr_params = [collection, field, branch_id]
            else:
                expr, expr_params = _metric_expr(metric_type, field, data_expr=data_expr)
        elif metric_type.startswith('relationCount'):
            agg_func = metric_type.replace('relationCount', '').upper()
            prefix = f"{table_alias}." if table_alias else ''
            subquery = (
                f"(SELECT COUNT(*) FROM data_relations dr "
                f"WHERE dr.collection = %s AND dr.record_id = {prefix}id "
                f"AND dr.field_name = %s AND dr.branch_id = %s)"
            )
            expr = f'{agg_func}({subquery})'
            expr_params = [collection, field, branch_id]
        else:
            expr, expr_params = _metric_expr(metric_type, field, data_expr=data_expr)

        if expr is None:
            raise ValueError(f"Unsupported metric: {metric_type}")
        select_parts.append(f'{expr} AS {metric["alias"]}')
        params.extend(expr_params)
    return select_parts, params


def _build_scalar_group_expr(group_by, data_expr='data'):
    """Build grouping expression for scalar fields."""
    group_field = group_by['field']
    group_type = group_by['type']

    if group_type == 'dateHistogram':
        trunc = DATE_TRUNC_MAP.get(group_by['interval'], 'month')
        return f"date_trunc('{trunc}', ({data_expr}->>%s)::timestamptz)", [group_field]
    if group_type == 'histogram':
        interval = group_by.get('interval')
        if interval in (None, 0, '0'):
            raise ValueError('Histogram interval must be greater than 0')
        interval_value = float(interval)
        offset_value = float(group_by.get('offset', 0) or 0)
        expr = (
            'CASE WHEN {data_expr}->>%s IS NULL THEN NULL '
            'ELSE FLOOR((({data_expr}->>%s)::numeric - %s) / %s) * %s + %s END'
        ).format(data_expr=data_expr)
        return expr, [group_field, group_field, offset_value, interval_value, interval_value, offset_value]
    if group_type == 'range':
        ranges = group_by.get('ranges') or []
        if not ranges:
            raise ValueError('Range grouping requires at least one range')

        parts = []
        params = []
        for idx, item in enumerate(ranges):
            key = item.get('key') or f'range_{idx + 1}'
            start = item.get('from')
            end = item.get('to')
            conditions = [f'{data_expr}->>%s IS NOT NULL']
            current_params = [group_field]
            if start is not None:
                conditions.append(f'({data_expr}->>%s)::numeric >= %s')
                current_params.extend([group_field, float(start)])
            if end is not None:
                conditions.append(f'({data_expr}->>%s)::numeric < %s')
                current_params.extend([group_field, float(end)])
            parts.append(f"WHEN {' AND '.join(conditions)} THEN %s")
            current_params.append(key)
            params.extend(current_params)

        return f"CASE {' '.join(parts)} ELSE %s END", params + ['(other)']
    if group_type == 'exists':
        return (
            "CASE WHEN NULLIF(BTRIM(COALESCE({data_expr}->>%s, '')), '') IS NULL "
            "THEN 'empty' ELSE 'nonEmpty' END"
        ).format(data_expr=data_expr), [group_field]

    return f'{data_expr}->>%s', [group_field]


def _format_metric_name(metric):
    return metric['name']


def _format_group_key(key, group_by):
    """Format group keys for API response."""
    if key is None:
        return '(空)'
    if hasattr(key, 'isoformat'):
        interval = group_by.get('interval', 'month')
        return key.strftime('%Y-%m-%d') if interval == 'day' else key.strftime('%Y-%m')
    if group_by.get('type') == 'histogram':
        numeric = float(key)
        return int(numeric) if numeric.is_integer() else numeric
    return str(key)


def _format_grouped_rows(rows, metrics, group_by):
    """Format grouped aggregation rows."""
    data = []
    for row in rows:
        key = _format_group_key(row[0], group_by)
        item = {'key': key}
        metric_values = {}
        for idx, metric in enumerate(metrics, start=1):
            raw_value = row[idx]
            metric_values[_format_metric_name(metric)] = float(raw_value) if raw_value is not None else 0
        item['value'] = metric_values[_format_metric_name(metrics[0])]
        if len(metrics) > 1:
            item['metrics'] = metric_values
        data.append(item)
    return data


def _format_single_row(row, metrics):
    """Format single-value aggregation result."""
    metric_values = {}
    for idx, metric in enumerate(metrics):
        raw_value = row[idx] if row and idx < len(row) else None
        metric_values[_format_metric_name(metric)] = float(raw_value) if raw_value is not None else 0

    payload = {
        'type': 'single',
        'value': metric_values[_format_metric_name(metrics[0])],
    }
    if len(metrics) > 1:
        payload['metrics'] = metric_values
    return payload


def _format_matrix_rows(rows, metrics, primary_group, breakdown_group):
    """Format two-dimensional grouped rows."""
    row_keys = []
    col_keys = []
    seen_rows = set()
    seen_cols = set()
    cells = []

    for row in rows:
        row_key = _format_group_key(row[0], primary_group)
        col_key = _format_group_key(row[1], breakdown_group)

        if row_key not in seen_rows:
            seen_rows.add(row_key)
            row_keys.append(row_key)
        if col_key not in seen_cols:
            seen_cols.add(col_key)
            col_keys.append(col_key)

        metric_values = {}
        for idx, metric in enumerate(metrics, start=2):
            raw_value = row[idx]
            metric_values[_format_metric_name(metric)] = float(raw_value) if raw_value is not None else 0

        cell = {
            'rowKey': row_key,
            'columnKey': col_key,
            'value': metric_values[_format_metric_name(metrics[0])],
        }
        if len(metrics) > 1:
            cell['metrics'] = metric_values
        cells.append(cell)

    return {
        'type': 'matrix',
        'rows': row_keys,
        'columns': col_keys,
        'data': cells,
    }


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
    try:
        metrics = _normalize_metrics(body)
        group_by = _normalize_group_by(body)
        breakdown_by = _normalize_optional_group_config(body.get('breakdownBy'))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # For matrix queries, ORDER BY uses row_key alias (not group_key)
    if breakdown_by:
        order_clause = SORT_MAP.get(sort_key, '{metric_alias} DESC').format(metric_alias=metrics[0]['alias'])
        # Replace group_key with row_key for key-based sorting
        if 'group_key' in order_clause:
            order_clause = order_clause.replace('group_key', 'row_key')
    else:
        order_clause = SORT_MAP.get(sort_key, '{metric_alias} DESC').format(metric_alias=metrics[0]['alias'])
    group_field = group_by['field']
    group_type = group_by['type']

    user = g.current_user
    branch_id = get_user_current_branch(user.get('userId', ''), collection)

    with get_db() as conn:
        cur = conn.cursor()

        if group_field:
            fields = _load_page_fields(cur, collection)
            field_def = next((f for f in fields if f.get('fieldName') == group_field), None)
            control_type = field_def.get('controlType', '') if field_def else ''
            breakdown_field_def = next((f for f in fields if f.get('fieldName') == breakdown_by['field']), None) if breakdown_by else None
            breakdown_control_type = breakdown_field_def.get('controlType', '') if breakdown_field_def else ''

            if breakdown_by:
                if control_type in ('relation', 'reference', 'quoteSelect') or breakdown_control_type in ('relation', 'reference', 'quoteSelect'):
                    return jsonify({"error": "二维交叉统计当前仅支持普通字段"}), 400

                where_clause, where_params = _build_where(collection, filter_query, branch_id=branch_id)
                try:
                    primary_expr, primary_params = _build_scalar_group_expr(group_by, data_expr='data')
                    secondary_expr, secondary_params = _build_scalar_group_expr(breakdown_by, data_expr='data')
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
                metric_selects, metric_params = _build_metric_selects(metrics, data_expr='data', collection=collection, branch_id=branch_id, table_alias='', fields=fields)

                sql = (
                    f"SELECT {primary_expr} AS row_key, {secondary_expr} AS column_key, {', '.join(metric_selects)} "
                    f"FROM dynamic_data WHERE {where_clause} "
                    f"GROUP BY row_key, column_key "
                    f"ORDER BY {order_clause} "
                    f"LIMIT %s"
                )
                params = primary_params + secondary_params + metric_params + where_params + [limit]
                cur.execute(sql, params)
                rows = cur.fetchall()
                return jsonify(_format_matrix_rows(rows, metrics, group_by, breakdown_by))

            if control_type == 'relation' and field_def:
                if group_type != 'terms':
                    return jsonify({"error": "Relation grouping only supports terms"}), 400
                rel_cfg = field_def.get('relationConfig', {})
                target_col = rel_cfg.get('targetCollection', '')
                display_field = rel_cfg.get('displayField', 'id')
                metric_selects, metric_params = _build_metric_selects(metrics, data_expr='dd.data', collection=collection, branch_id=branch_id, table_alias='dd', fields=fields)
                where_clause, where_params = _build_where(collection, filter_query, table_alias='dd', branch_id=branch_id)

                sql = (
                    f"SELECT tgt.data->>%s AS group_key, {', '.join(metric_selects)} "
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
                if group_type != 'terms':
                    return jsonify({"error": "Reference grouping only supports terms"}), 400
                ref_cfg = field_def.get('referenceConfig', {})
                target_col = ref_cfg.get('targetCollection', '')
                display_field = ref_cfg.get('displayField', 'id')
                metric_selects, metric_params = _build_metric_selects(metrics, data_expr='dd.data', collection=collection, branch_id=branch_id, table_alias='dd', fields=fields)
                where_clause, where_params = _build_where(collection, filter_query, table_alias='dd', branch_id=branch_id)

                sql = (
                    f"SELECT tgt.data->>%s AS group_key, {', '.join(metric_selects)} "
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
                if group_type != 'terms':
                    return jsonify({"error": "QuoteSelect grouping only supports terms"}), 400
                quote_cfg = field_def.get('quoteConfig', {})
                target_col = quote_cfg.get('targetCollection', '')
                display_field = quote_cfg.get('displayField', 'id')
                metric_selects, metric_params = _build_metric_selects(metrics, data_expr='dd.data', collection=collection, branch_id=branch_id, table_alias='dd', fields=fields)
                where_clause, where_params = _build_where(collection, filter_query, table_alias='dd', branch_id=branch_id)

                sql = (
                    f"SELECT tgt.data->>%s AS group_key, {', '.join(metric_selects)} "
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
                where_clause, where_params = _build_where(collection, filter_query, branch_id=branch_id)
                try:
                    group_expr, group_params = _build_scalar_group_expr(group_by, data_expr='data')
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
                metric_selects, metric_params = _build_metric_selects(metrics, data_expr='data', collection=collection, branch_id=branch_id, table_alias='', fields=fields)

                sql = (
                    f"SELECT {group_expr} AS group_key, {', '.join(metric_selects)} "
                    f"FROM dynamic_data WHERE {where_clause} "
                    f"GROUP BY group_key "
                    f"ORDER BY {order_clause} "
                    f"LIMIT %s"
                )
                params = group_params + metric_params + where_params + [limit]

            cur.execute(sql, params)
            rows = cur.fetchall()
            return jsonify({'type': 'grouped', 'data': _format_grouped_rows(rows, metrics, group_by)})

        # No grouping - single aggregate value
        fields = _load_page_fields(cur, collection)
        metric_selects, metric_params = _build_metric_selects(metrics, data_expr='data', collection=collection, branch_id=branch_id, table_alias='', fields=fields)
        where_clause, where_params = _build_where(collection, filter_query, branch_id=branch_id)
        sql = f"SELECT {', '.join(metric_selects)} FROM dynamic_data WHERE {where_clause}"
        params = metric_params + where_params
        cur.execute(sql, params)
        row = cur.fetchone()
        return jsonify(_format_single_row(row, metrics))
