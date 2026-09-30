from flask import Blueprint, jsonify, g as flask_g
from db import get_db
from auth import login_required
from utils.version import get_user_current_branch, MAIN_BRANCH_ID
import json

relation_graph_bp = Blueprint('relation_graph', __name__)


def _get_display_name(data, fields):
    """Get display name from the first non-auto field."""
    if not data or not fields:
        return ''
    for f in fields:
        ct = f.get('controlType', '')
        if ct in ('autoTimestamp', 'autoSequence'):
            continue
        val = data.get(f.get('fieldName', ''))
        if val and isinstance(val, str):
            return val
    return ''


def _get_field_label(fields, field_name):
    """Get field label from fields list."""
    for f in fields:
        if f.get('fieldName') == field_name:
            return f.get('label', field_name)
    return field_name


def _get_current_user_branch(collection):
    """Get the current user's branch for the given collection."""
    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    user_id = user.get('userId')
    if not user_id:
        return MAIN_BRANCH_ID
    return get_user_current_branch(user_id, collection)


@relation_graph_bp.route('/relation-graph/<collection>/<record_id>', methods=['GET'])
@login_required
def get_relation_graph(collection, record_id):
    edges = []
    seen_edges = set()
    # Collect (id, collection) pairs to batch-fetch at the end
    pending_nodes = {}  # id -> collection
    branch_cache = {}

    with get_db() as conn:
        cur = conn.cursor()

        def branch_for(target_collection):
            if target_collection not in branch_cache:
                branch_cache[target_collection] = _get_current_user_branch(target_collection)
            return branch_cache[target_collection]

        # ── Pre-load all page_configs in ONE query ──
        cur.execute('SELECT id, name, fields FROM page_configs')
        pc_rows = cur.fetchall()
        page_cache = {}  # collection -> (label, fields)
        for pc_id, pc_name, pc_fields in pc_rows:
            col = pc_id.replace('page-', '', 1)
            page_cache[col] = (pc_name, pc_fields or [])

        # ── 1. Center record ──
        center_branch_id = branch_for(collection)
        cur.execute(
            'SELECT id, data FROM dynamic_data WHERE id = %s AND collection = %s AND branch_id = %s',
            (record_id, collection, center_branch_id),
        )
        center_row = cur.fetchone()
        if not center_row:
            return jsonify({'error': 'Record not found'}), 404

        center_data = center_row[1] or {}
        page_label, page_fields = page_cache.get(collection, (collection, []))

        center_display = _get_display_name(center_data, page_fields) or record_id
        # Center node is already resolved — store directly
        resolved_nodes = {
            record_id: {
                'id': record_id,
                'label': center_display,
                'collection': collection,
                'collectionLabel': page_label,
                'data': center_data,
            }
        }

        # ── 2. Forward M2M ──
        cur.execute(
            'SELECT field_name, related_collection, related_id '
            'FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
            (collection, record_id, center_branch_id),
        )
        for field_name, related_col, related_id in cur.fetchall():
            edge_key = (record_id, related_id, field_name, 'relation')
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append({
                    'source': record_id,
                    'target': related_id,
                    'label': _get_field_label(page_fields, field_name),
                    'relType': 'relation',
                })
            if related_id not in resolved_nodes:
                pending_nodes[related_id] = related_col

        # ── 3. Reverse M2M ──
        cur.execute(
            'SELECT collection, record_id, field_name, branch_id '
            'FROM data_relations WHERE related_collection = %s AND related_id = %s',
            (collection, record_id),
        )
        for src_col, src_id, field_name, branch_id in cur.fetchall():
            if branch_id != branch_for(src_col):
                continue
            edge_key = (src_id, record_id, field_name, 'relation')
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            src_fields = page_cache.get(src_col, (src_col, []))[1]
            edges.append({
                'source': src_id,
                'target': record_id,
                'label': _get_field_label(src_fields, field_name),
                'relType': 'relation',
            })
            if src_id not in resolved_nodes:
                pending_nodes[src_id] = src_col

        # ── 4. Forward reference ──
        for f in page_fields:
            if f.get('controlType') != 'reference':
                continue
            ref_config = f.get('referenceConfig') or {}
            target_col = ref_config.get('targetCollection', '')
            if not target_col:
                continue
            parent_id = center_data.get(f.get('fieldName', ''))
            if not parent_id or not isinstance(parent_id, str):
                continue

            edge_key = (record_id, parent_id, f['fieldName'], 'reference')
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append({
                    'source': record_id,
                    'target': parent_id,
                    'label': f.get('label', f['fieldName']),
                    'relType': 'reference',
                })
            if parent_id not in resolved_nodes:
                pending_nodes[parent_id] = target_col

        # ── 5. Reverse reference — batch by (src_col, field_name) ──
        ref_queries = []  # [(src_col, field_name, field_label)]
        for col, (_, fields) in page_cache.items():
            for f in fields:
                if f.get('controlType') != 'reference':
                    continue
                rc = f.get('referenceConfig') or {}
                if rc.get('targetCollection') != collection:
                    continue
                fn = f.get('fieldName', '')
                if fn:
                    ref_queries.append((col, fn, f.get('label', fn)))

        if ref_queries:
            # Build a UNION ALL query to fetch all reverse-reference children in one round-trip
            parts = []
            params = []
            for src_col, fn, _ in ref_queries:
                parts.append(
                    "SELECT id, data, collection FROM dynamic_data "
                    "WHERE collection = %s AND branch_id = %s AND data->>%s = %s"
                )
                params.extend([src_col, branch_for(src_col), fn, record_id])

            if parts:
                sql = ' UNION ALL '.join(parts)
                cur.execute(sql, params)
                # Map results back by collection to attach correct field labels
                child_by_col = {}
                for child_id, child_data, child_col in cur.fetchall():
                    child_by_col.setdefault(child_col, []).append((child_id, child_data))

                for src_col, fn, fl in ref_queries:
                    for child_id, child_data in child_by_col.get(src_col, []):
                        edge_key = (child_id, record_id, fn, 'reference')
                        if edge_key in seen_edges:
                            continue
                        seen_edges.add(edge_key)
                        edges.append({
                            'source': child_id,
                            'target': record_id,
                            'label': fl,
                            'relType': 'reference',
                        })
                        if child_id not in resolved_nodes:
                            pending_nodes[child_id] = src_col

        # ── 6. Forward quoteSelect ──
        for f in page_fields:
            if f.get('controlType') != 'quoteSelect':
                continue
            qc = f.get('quoteConfig') or {}
            target_col = qc.get('targetCollection', '')
            if not target_col:
                continue
            fn = f.get('fieldName', '')
            quoted_ids = center_data.get(fn)
            if not quoted_ids or not isinstance(quoted_ids, list):
                continue

            fl = f.get('label', fn)
            for qid in quoted_ids:
                if not isinstance(qid, str):
                    continue
                edge_key = (record_id, qid, fn, 'quoteSelect')
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                edges.append({
                    'source': record_id,
                    'target': qid,
                    'label': fl,
                    'relType': 'quoteSelect',
                })
                if qid not in resolved_nodes:
                    pending_nodes[qid] = target_col

        # ── 7. Reverse quoteSelect — batch by (src_col, field_name) ──
        quote_queries = []
        for col, (_, fields) in page_cache.items():
            for f in fields:
                if f.get('controlType') != 'quoteSelect':
                    continue
                qc = f.get('quoteConfig') or {}
                if qc.get('targetCollection') != collection:
                    continue
                fn = f.get('fieldName', '')
                if fn:
                    quote_queries.append((col, fn, f.get('label', fn)))

        if quote_queries:
            parts = []
            params = []
            json_val = json.dumps([record_id])
            for src_col, fn, _ in quote_queries:
                parts.append(
                    "SELECT id, data, collection FROM dynamic_data "
                    "WHERE collection = %s AND branch_id = %s AND data->%s @> %s::jsonb"
                )
                params.extend([src_col, branch_for(src_col), fn, json_val])

            if parts:
                sql = ' UNION ALL '.join(parts)
                cur.execute(sql, params)
                q_by_col = {}
                for q_id, q_data, q_col in cur.fetchall():
                    q_by_col.setdefault(q_col, []).append((q_id, q_data))

                for src_col, fn, fl in quote_queries:
                    for q_id, q_data in q_by_col.get(src_col, []):
                        edge_key = (q_id, record_id, fn, 'quoteSelect')
                        if edge_key in seen_edges:
                            continue
                        seen_edges.add(edge_key)
                        edges.append({
                            'source': q_id,
                            'target': record_id,
                            'label': fl,
                            'relType': 'quoteSelect',
                        })
                        if q_id not in resolved_nodes:
                            pending_nodes[q_id] = src_col

        # ── 8. Batch-fetch ALL pending node display names in ONE query ──
        # Remove already-resolved IDs
        ids_to_fetch = [nid for nid in pending_nodes if nid not in resolved_nodes]
        if ids_to_fetch:
            conditions = []
            params = []
            for nid in ids_to_fetch:
                ncol = pending_nodes[nid]
                conditions.append('(id = %s AND collection = %s AND branch_id = %s)')
                params.extend([nid, ncol, branch_for(ncol)])

            cur.execute(
                'SELECT id, collection, data FROM dynamic_data WHERE ' + ' OR '.join(conditions),
                params,
            )
            fetched = {}
            for nid, ncol, ndata in cur.fetchall():
                fetched[(nid, ncol)] = ndata or {}

            for nid in ids_to_fetch:
                ncol = pending_nodes[nid]
                p_label, p_fields = page_cache.get(ncol, (ncol, []))
                if (nid, ncol) in fetched:
                    ndata = fetched[(nid, ncol)]
                    display = _get_display_name(ndata, p_fields) or nid
                else:
                    ndata = {}
                    display = nid
                resolved_nodes[nid] = {
                    'id': nid,
                    'label': display,
                    'collection': ncol,
                    'collectionLabel': p_label,
                    'data': ndata,
                }

    return jsonify({
        'nodes': list(resolved_nodes.values()),
        'edges': edges,
        'centerId': record_id,
    })
