"""Read-only query engine for the MCP server: MongoDB-style filter -> rows,
with cross-collection lookups, plus xlsx export.

Ported from server/routes/query.py (execute_query + helpers). Keep the lookup
logic in sync with that file.
"""
import json
from openpyxl import Workbook
from mongo_query import translate as mongo_translate, remap_labels

MAX_TABLE_ROWS = 400
MAX_XLSX_ROWS = 50000


def load_page_configs(cur):
    cur.execute('SELECT id, name, fields FROM page_configs')
    result = {}
    for pid, pname, pfields in cur.fetchall():
        col = pid.replace('page-', '', 1)
        result[col] = {'name': pname, 'fields': pfields or []}
    return result


def _build_label_map(fields):
    m = {}
    for f in fields:
        fn = f.get('fieldName', '')
        lb = f.get('label', '')
        if fn and lb:
            m[lb] = fn
    return m


def _detect_relation_type(fields, field_name):
    for f in fields:
        if f.get('fieldName') != field_name:
            continue
        ct = f.get('controlType', '')
        if ct == 'relation':
            return 'relation', (f.get('relationConfig') or {}).get('targetCollection', '')
        if ct == 'reference':
            return 'reference', (f.get('referenceConfig') or {}).get('targetCollection', '')
        if ct == 'quoteSelect':
            return 'quoteSelect', (f.get('quoteConfig') or {}).get('targetCollection', '')
    return None, None


def _order_clause(sort_spec, label_map):
    parts = []
    for k, direction in (sort_spec or {}).items():
        fname = label_map.get(k, k)
        if fname == 'createdAt':
            col = 'created_at'
        elif fname == 'updatedAt':
            col = 'updated_at'
        else:
            col = f"data->>'{fname.replace(chr(39), chr(39) + chr(39))}'"
        parts.append(f"{col} {'ASC' if direction >= 0 else 'DESC'}")
    return ', '.join(parts) if parts else 'created_at'


def count_rows(cur, collection, query, fields):
    q = remap_labels(query, fields) if query else {}
    where, params = mongo_translate(q)
    cur.execute(
        'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND (' + where + ')',
        [collection] + params,
    )
    return cur.fetchone()[0]


def _apply_lookups(cur, records, collection, col_fields, configs, lookups, label_map):
    for lk in lookups:
        from_col = lk.get('from', '')
        local_field = label_map.get(lk.get('localField', ''), lk.get('localField', ''))
        as_name = lk.get('as', from_col)
        if not from_col or not local_field:
            continue
        rel_type, target_col = _detect_relation_type(col_fields, local_field)
        if not target_col:
            target_col = from_col

        if rel_type == 'relation':
            record_ids = [r['_id'] for r in records]
            rel_map, all_related = {}, set()
            if record_ids:
                cur.execute(
                    'SELECT record_id, related_id FROM data_relations '
                    'WHERE collection = %s AND field_name = %s AND record_id = ANY(%s)',
                    (collection, local_field, record_ids),
                )
                for src_id, rel_id in cur.fetchall():
                    rel_map.setdefault(src_id, []).append(rel_id)
                    all_related.add(rel_id)
            related_data = {}
            if all_related:
                cur.execute('SELECT id, data FROM dynamic_data WHERE id = ANY(%s)', (list(all_related),))
                for fid, fdata in cur.fetchall():
                    related_data[fid] = fdata or {}
            for rec in records:
                rec[as_name] = [{'_id': rid, **related_data.get(rid, {})} for rid in rel_map.get(rec['_id'], [])]

        elif rel_type == 'reference':
            parent_ids = {rec.get(local_field) for rec in records if isinstance(rec.get(local_field), str)}
            parent_data = {}
            if parent_ids:
                cur.execute('SELECT id, data FROM dynamic_data WHERE id = ANY(%s)', (list(parent_ids),))
                for fid, fdata in cur.fetchall():
                    parent_data[fid] = fdata or {}
            for rec in records:
                pid = rec.get(local_field)
                rec[as_name] = {'_id': pid, **parent_data[pid]} if pid in parent_data else None

        elif rel_type == 'quoteSelect':
            all_qids = set()
            for rec in records:
                if isinstance(rec.get(local_field), list):
                    all_qids.update(rec[local_field])
            quoted = {}
            if all_qids:
                cur.execute('SELECT id, data FROM dynamic_data WHERE id = ANY(%s)', (list(all_qids),))
                for fid, fdata in cur.fetchall():
                    quoted[fid] = fdata or {}
            for rec in records:
                qids = rec.get(local_field, [])
                rec[as_name] = [{'_id': q, **quoted.get(q, {})} for q in qids] if isinstance(qids, list) else []

        else:
            ids = set()
            for rec in records:
                v = rec.get(local_field)
                if isinstance(v, str):
                    ids.add(v)
                elif isinstance(v, list):
                    ids.update(x for x in v if isinstance(x, str))
            fetched = {}
            if ids:
                cur.execute(
                    'SELECT id, data FROM dynamic_data WHERE collection = %s AND id = ANY(%s)',
                    (target_col, list(ids)),
                )
                for fid, fdata in cur.fetchall():
                    fetched[fid] = fdata or {}
            for rec in records:
                v = rec.get(local_field)
                if isinstance(v, str):
                    rec[as_name] = {'_id': v, **fetched.get(v, {})} if v in fetched else None
                elif isinstance(v, list):
                    rec[as_name] = [{'_id': x, **fetched.get(x, {})} for x in v if x in fetched]
                else:
                    rec[as_name] = None


def _build_columns(records, col_fields, lookups, configs):
    label_map = {f.get('fieldName'): f.get('label', f.get('fieldName')) for f in col_fields}
    lookup_as = {lk.get('as', lk.get('from', '')): lk.get('from', '') for lk in lookups}
    columns, seen = [], set()
    if not records:
        return columns
    for key in records[0]:
        if key in seen:
            continue
        seen.add(key)
        col = {'key': key, 'label': label_map.get(key, key)}
        if key in lookup_as:
            tgt = lookup_as[key]
            col['label'] = f"{configs.get(tgt, {}).get('name', tgt)} ({key})"
            col['isLookup'] = True
        columns.append(col)
    return columns


def run_query(cur, collection, configs, query, lookups, select, sort, skip, limit):
    """Return {total, rows, columns} for `collection` matching `query`."""
    col_fields = configs[collection]['fields']
    label_map = _build_label_map(col_fields)
    q = remap_labels(query, col_fields) if query else {}
    where, params = mongo_translate(q)
    order = _order_clause(sort, label_map)

    cur.execute(
        'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND (' + where + ')',
        [collection] + params,
    )
    total = cur.fetchone()[0]

    cur.execute(
        'SELECT id, data, created_at, updated_at FROM dynamic_data '
        'WHERE collection = %s AND (' + where + ') ORDER BY ' + order + ' LIMIT %s OFFSET %s',
        [collection] + params + [limit, skip],
    )
    records = []
    for rid, data, c_at, u_at in cur.fetchall():
        rec = {'_id': rid}
        if data:
            rec.update(data)
        if c_at:
            rec['createdAt'] = c_at.isoformat() if hasattr(c_at, 'isoformat') else str(c_at)
        if u_at:
            rec['updatedAt'] = u_at.isoformat() if hasattr(u_at, 'isoformat') else str(u_at)
        records.append(rec)

    if lookups:
        _apply_lookups(cur, records, collection, col_fields, configs, lookups, label_map)

    select_fields = [label_map.get(s, s) for s in (select or [])]
    if select_fields:
        keep = set(select_fields) | {'_id', 'createdAt', 'updatedAt'} | \
            {lk.get('as', lk.get('from', '')) for lk in lookups}
        records = [{k: v for k, v in r.items() if k in keep} for r in records]

    return {'total': total, 'rows': records, 'columns': _build_columns(records, col_fields, lookups, configs)}


def _cell(v):
    if v is None:
        return ''
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return '是' if v else '否'
    return v


def write_xlsx(rows, columns, out_path, sheet_title='data'):
    wb = Workbook()
    sh = wb.active
    sh.title = (sheet_title or 'data')[:31]
    headers = [c['label'] for c in columns] or ['(空)']
    keys = [c['key'] for c in columns]
    sh.append(headers)
    for r in rows:
        sh.append([_cell(r.get(k)) for k in keys])
    wb.save(out_path)
