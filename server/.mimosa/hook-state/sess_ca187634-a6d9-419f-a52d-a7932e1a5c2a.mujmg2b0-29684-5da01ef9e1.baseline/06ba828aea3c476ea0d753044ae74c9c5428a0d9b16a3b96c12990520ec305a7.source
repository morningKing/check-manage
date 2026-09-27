"""Advanced query endpoint with cross-collection lookup support."""

from flask import Blueprint, request, jsonify
from db import get_db
from auth import login_required
from utils.mongo_query import translate as mongo_translate, remap_labels, MongoQueryError
import json

query_bp = Blueprint('query', __name__)


def _get_all_page_configs(cur):
    """Load all page configs: {collection: {name, fields}}."""
    cur.execute('SELECT id, name, fields FROM page_configs')
    result = {}
    for pid, pname, pfields in cur.fetchall():
        col = pid.replace('page-', '', 1)
        result[col] = {'name': pname, 'fields': pfields or []}
    return result


def _build_label_map(fields):
    """Build label→fieldName mapping."""
    m = {}
    for f in fields:
        fn = f.get('fieldName', '')
        lb = f.get('label', '')
        if fn and lb:
            m[lb] = fn
    return m


def _detect_relation_type(fields, field_name):
    """Detect how a field relates to another collection."""
    for f in fields:
        if f.get('fieldName') != field_name:
            continue
        ct = f.get('controlType', '')
        if ct == 'relation':
            rc = f.get('relationConfig') or {}
            return 'relation', rc.get('targetCollection', '')
        if ct == 'reference':
            rc = f.get('referenceConfig') or {}
            return 'reference', rc.get('targetCollection', '')
        if ct == 'quoteSelect':
            qc = f.get('quoteConfig') or {}
            return 'quoteSelect', qc.get('targetCollection', '')
    return None, None


@query_bp.route('/query/collections', methods=['GET'])
@login_required
def list_query_collections():
    """List all collections with their fields for the query builder UI."""
    with get_db() as conn:
        cur = conn.cursor()
        configs = _get_all_page_configs(cur)

    result = []
    for col, info in sorted(configs.items(), key=lambda x: x[1]['name']):
        fields = []
        for f in info['fields']:
            fd = {
                'fieldName': f.get('fieldName', ''),
                'label': f.get('label', ''),
                'controlType': f.get('controlType', ''),
            }
            if f.get('controlType') == 'relation':
                rc = f.get('relationConfig') or {}
                fd['targetCollection'] = rc.get('targetCollection', '')
            elif f.get('controlType') == 'reference':
                rc = f.get('referenceConfig') or {}
                fd['targetCollection'] = rc.get('targetCollection', '')
            elif f.get('controlType') == 'quoteSelect':
                qc = f.get('quoteConfig') or {}
                fd['targetCollection'] = qc.get('targetCollection', '')
            if f.get('options'):
                fd['options'] = f['options']
            fields.append(fd)
        result.append({
            'collection': col,
            'name': info['name'],
            'fields': fields,
        })
    return jsonify(result)


@query_bp.route('/query/execute', methods=['POST'])
@login_required
def execute_query():
    """Execute an advanced query with optional cross-collection lookups.

    Request body:
    {
      "collection": "inspection-case",
      "query": {"用例ID": {"$regex": "IC"}},
      "lookup": [
        {"from": "inspection-template", "localField": "templateids", "as": "templates"}
      ],
      "select": ["caseid", "caseName"],   // optional projection (fieldName or label)
      "sort": {"caseid": 1},              // 1=asc, -1=desc
      "skip": 0,
      "limit": 200
    }
    """
    body = request.get_json(force=True)
    collection = body.get('collection', '')
    query = body.get('query') or {}
    lookups = body.get('lookup') or []
    select_fields = body.get('select') or []
    sort_spec = body.get('sort') or {}
    skip = max(int(body.get('skip', 0)), 0)
    limit = min(max(int(body.get('limit', 200)), 1), 2000)

    if not collection:
        return jsonify({'error': '请指定集合 (collection)'}), 400

    with get_db() as conn:
        cur = conn.cursor()
        configs = _get_all_page_configs(cur)

        if collection not in configs:
            return jsonify({'error': f'集合不存在: {collection}'}), 404

        col_fields = configs[collection]['fields']
        label_map = _build_label_map(col_fields)
        field_name_set = {f.get('fieldName') for f in col_fields}

        # Remap query labels → fieldNames
        if query:
            query = remap_labels(query, col_fields)

        # Remap sort labels → fieldNames
        remapped_sort = {}
        for k, v in sort_spec.items():
            remapped_sort[label_map.get(k, k)] = v
        sort_spec = remapped_sort

        # Remap select labels → fieldNames
        select_fields = [label_map.get(s, s) for s in select_fields]

        # Build WHERE clause
        try:
            where_fragment, q_params = mongo_translate(query)
        except MongoQueryError as e:
            return jsonify({'error': f'查询语法错误: {e}'}), 400

        # Build ORDER BY
        order_parts = []
        for fname, direction in sort_spec.items():
            if fname == 'createdAt':
                col_expr = 'created_at'
            elif fname == 'updatedAt':
                col_expr = 'updated_at'
            else:
                col_expr = f"data->>'{fname.replace(chr(39), chr(39)+chr(39))}'"
            order_parts.append(f"{col_expr} {'ASC' if direction >= 0 else 'DESC'}")
        order_clause = ', '.join(order_parts) if order_parts else 'created_at'

        # Count
        count_sql = (
            'SELECT COUNT(*) FROM dynamic_data '
            'WHERE collection = %s AND (' + where_fragment + ')'
        )
        cur.execute(count_sql, [collection] + q_params)
        total = cur.fetchone()[0]

        # Fetch main data
        main_sql = (
            'SELECT id, data, created_at, updated_at FROM dynamic_data '
            'WHERE collection = %s AND (' + where_fragment + ') '
            'ORDER BY ' + order_clause + ' '
            'LIMIT %s OFFSET %s'
        )
        cur.execute(main_sql, [collection] + q_params + [limit, skip])
        rows = cur.fetchall()

        records = []
        for rid, data, created_at, updated_at in rows:
            rec = {'_id': rid}
            if data:
                rec.update(data)
            if created_at:
                rec['createdAt'] = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at)
            if updated_at:
                rec['updatedAt'] = updated_at.isoformat() if hasattr(updated_at, 'isoformat') else str(updated_at)
            records.append(rec)

        # Process lookups
        for lk in lookups:
            from_col = lk.get('from', '')
            local_field = lk.get('localField', '')
            as_name = lk.get('as', from_col)

            if not from_col or not local_field:
                continue

            # Remap local field label
            local_field = label_map.get(local_field, local_field)
            as_name_actual = as_name

            # Detect relationship type
            rel_type, target_col = _detect_relation_type(col_fields, local_field)

            if not target_col:
                target_col = from_col

            target_fields = configs.get(target_col, {}).get('fields', [])

            if rel_type == 'relation':
                # M:N via data_relations table
                record_ids = [r['_id'] for r in records]
                if record_ids:
                    cur.execute(
                        'SELECT record_id, related_id FROM data_relations '
                        'WHERE collection = %s AND field_name = %s AND record_id = ANY(%s)',
                        (collection, local_field, record_ids),
                    )
                    rel_map = {}
                    all_related = set()
                    for src_id, rel_id in cur.fetchall():
                        rel_map.setdefault(src_id, []).append(rel_id)
                        all_related.add(rel_id)

                    # Fetch related records
                    related_data = {}
                    if all_related:
                        cur.execute(
                            'SELECT id, data FROM dynamic_data WHERE id = ANY(%s)',
                            (list(all_related),),
                        )
                        for fid, fdata in cur.fetchall():
                            related_data[fid] = fdata or {}

                    for rec in records:
                        related_ids = rel_map.get(rec['_id'], [])
                        rec[as_name_actual] = [
                            {'_id': rid, **related_data.get(rid, {})}
                            for rid in related_ids
                        ]

            elif rel_type == 'reference':
                # 1:N parent reference — local_field stores parent id
                parent_ids = set()
                for rec in records:
                    pid = rec.get(local_field)
                    if pid and isinstance(pid, str):
                        parent_ids.add(pid)

                parent_data = {}
                if parent_ids:
                    cur.execute(
                        'SELECT id, data FROM dynamic_data WHERE id = ANY(%s)',
                        (list(parent_ids),),
                    )
                    for fid, fdata in cur.fetchall():
                        parent_data[fid] = fdata or {}

                for rec in records:
                    pid = rec.get(local_field)
                    if pid and pid in parent_data:
                        rec[as_name_actual] = {'_id': pid, **parent_data[pid]}
                    else:
                        rec[as_name_actual] = None

            elif rel_type == 'quoteSelect':
                # Quote select — local_field stores array of ids
                all_qids = set()
                for rec in records:
                    qids = rec.get(local_field)
                    if isinstance(qids, list):
                        all_qids.update(qids)

                quoted_data = {}
                if all_qids:
                    cur.execute(
                        'SELECT id, data FROM dynamic_data WHERE id = ANY(%s)',
                        (list(all_qids),),
                    )
                    for fid, fdata in cur.fetchall():
                        quoted_data[fid] = fdata or {}

                for rec in records:
                    qids = rec.get(local_field, [])
                    if isinstance(qids, list):
                        rec[as_name_actual] = [
                            {'_id': qid, **quoted_data.get(qid, {})}
                            for qid in qids
                        ]
                    else:
                        rec[as_name_actual] = []

            else:
                # Generic: try direct id match (local field value = target record id)
                ids_to_fetch = set()
                for rec in records:
                    val = rec.get(local_field)
                    if isinstance(val, str):
                        ids_to_fetch.add(val)
                    elif isinstance(val, list):
                        ids_to_fetch.update(v for v in val if isinstance(v, str))

                fetched = {}
                if ids_to_fetch:
                    cur.execute(
                        'SELECT id, data FROM dynamic_data '
                        'WHERE collection = %s AND id = ANY(%s)',
                        (target_col, list(ids_to_fetch)),
                    )
                    for fid, fdata in cur.fetchall():
                        fetched[fid] = fdata or {}

                for rec in records:
                    val = rec.get(local_field)
                    if isinstance(val, str):
                        rec[as_name_actual] = {'_id': val, **fetched.get(val, {})} if val in fetched else None
                    elif isinstance(val, list):
                        rec[as_name_actual] = [
                            {'_id': v, **fetched.get(v, {})}
                            for v in val if isinstance(v, str) and v in fetched
                        ]
                    else:
                        rec[as_name_actual] = None

        # Apply projection
        if select_fields:
            projected = []
            keep = set(select_fields) | {'_id', 'createdAt', 'updatedAt'}
            # Also keep lookup "as" names
            for lk in lookups:
                keep.add(lk.get('as', lk.get('from', '')))
            for rec in records:
                projected.append({k: v for k, v in rec.items() if k in keep})
            records = projected

        # Build column metadata for the frontend
        columns = _build_columns(records, col_fields, lookups, configs, select_fields)

    return jsonify({
        'data': records,
        'total': total,
        'skip': skip,
        'limit': limit,
        'columns': columns,
    })


def _build_columns(records, col_fields, lookups, configs, select_fields):
    """Build column definitions for the result table."""
    columns = []
    seen = set()
    label_map = {f.get('fieldName'): f.get('label', f.get('fieldName')) for f in col_fields}

    if not records:
        return columns

    sample = records[0]
    for key in sample:
        if key in seen:
            continue
        seen.add(key)
        label = label_map.get(key, key)
        col = {'key': key, 'label': label}

        # Check if this is a lookup result
        for lk in lookups:
            as_name = lk.get('as', lk.get('from', ''))
            if key == as_name:
                target_col = lk.get('from', '')
                target_info = configs.get(target_col, {})
                col['label'] = f"{target_info.get('name', target_col)} ({as_name})"
                col['isLookup'] = True
                break

        columns.append(col)

    return columns
