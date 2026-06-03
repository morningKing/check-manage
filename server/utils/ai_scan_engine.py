import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from db import get_db
from utils.batch_repo import create_batch
from utils.ai_scan_repo import mark_run


def extract_json(text):
    """Return the parsed JSON object from the AI reply, or None.
    Prefers the last ```json fenced block; falls back to the last balanced {...}."""
    if not text:
        return None
    blocks = re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    for raw in reversed(blocks):
        try:
            return json.loads(raw)
        except ValueError:
            continue
    # fallback: scan for balanced top-level objects, try the last parseable one
    candidates = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start:i + 1])
    for raw in reversed(candidates):
        try:
            return json.loads(raw)
        except ValueError:
            continue
    return None


def message_text(final_msg):
    """Concatenate the text parts of an assistant message dict."""
    if not final_msg:
        return ''
    parts = [p.get('text', '') for p in (final_msg.get('content') or [])
             if p.get('type') == 'text']
    return '\n'.join(t for t in parts if t)


def assemble_prompt(task):
    """[system preamble] + [user prompt_template] + [system JSON output contract]."""
    keys = [m['jsonKey'] for m in (task.get('field_mapping') or [])]
    contract_obj = ', '.join(f'"{k}": ...' for k in keys)
    preamble = ('本任务的数据见工作区 uploads/record.md，附件见 uploads/attachments/ 目录。\n'
                '请阅读这些内容后完成下面的任务。\n\n')
    contract = ('\n\n---\n完成后，请在回复的最后输出一个 JSON 代码块，'
                f'且仅包含以下字段：\n```json\n{{ {contract_obj} }}\n```')
    return preamble + (task.get('prompt_template') or '') + contract


def _load_task(task_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, collection, branch_id, status_field, done_value, "
                "failed_value, field_mapping FROM ai_scan_tasks WHERE id = %s",
                (task_id,),
            )
            r = cur.fetchone()
            if not r:
                return None
            return {'id': r[0], 'collection': r[1], 'branch_id': r[2],
                    'status_field': r[3], 'done_value': r[4], 'failed_value': r[5],
                    'field_mapping': r[6] or []}


def _set_record_status(task, record_id, value):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE dynamic_data SET data = jsonb_set(data, ARRAY[%s], to_jsonb(%s::text)), "
                "updated_at = now(), version = version + 1 "
                "WHERE id = %s AND collection = %s AND branch_id = %s",
                (task['status_field'], value, record_id, task['collection'], task['branch_id']),
            )
        conn.commit()


def _write_back(task, record_id, parsed):
    """Apply mapped columns + done_value in one UPDATE. Returns rowcount."""
    # build nested jsonb_set: start from `data`, wrap once per mapped column + status
    expr = 'data'
    params = []
    for m in task['field_mapping']:
        val = parsed.get(m['jsonKey'])
        expr = f"jsonb_set({expr}, ARRAY[%s], to_jsonb(%s::text))"
        params.extend([m['column'], '' if val is None else str(val)])
    expr = f"jsonb_set({expr}, ARRAY[%s], to_jsonb(%s::text))"
    params.extend([task['status_field'], task['done_value']])
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE dynamic_data SET data = {expr}, updated_at = now(), "
                "version = version + 1 WHERE id = %s AND collection = %s AND branch_id = %s",
                params + [record_id, task['collection'], task['branch_id']],
            )
            n = cur.rowcount
        conn.commit()
    return n


def on_child_finished(session_row, final_msg, ok):
    task = _load_task(session_row.get('scan_task_id'))
    if not task:
        return
    rid = session_row.get('source_record_id')
    if not rid:
        return
    if not ok:
        _set_record_status(task, rid, task['failed_value'])
        return
    parsed = extract_json(message_text(final_msg))
    required = [m['jsonKey'] for m in task['field_mapping'] if m.get('required')]
    if parsed is None or any(parsed.get(k) in (None, '') for k in required):
        _set_record_status(task, rid, task['failed_value'])
        return
    n = _write_back(task, rid, parsed)
    if n == 0:
        print(f"[ai_scan] write-back matched 0 rows for record {rid}")
        return


def _workspace_root():
    return os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')


def _page_config_fields(collection):
    """The `fields` list of the collection's page config (or [])."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT fields FROM page_configs WHERE id = %s",
                        (f'page-{collection}',))
            r = cur.fetchone()
    return (r[0] if r else []) or []


def _field_labels(collection):
    """fieldName -> label from the page config. Reuses operation_log helper.

    Note: get_field_label_map(fields) takes the fields list (verified against
    utils/operation_log.py), so we resolve the page config fields first.
    """
    from utils.operation_log import get_field_label_map
    return get_field_label_map(_page_config_fields(collection))


def _file_field_names(collection):
    """Names of file/image controlType fields in the page config."""
    fields = _page_config_fields(collection)
    return [f['fieldName'] for f in fields
            if f.get('controlType') in ('file', 'image')]


def _render_record_md(data, labels, exclude=None):
    exclude = exclude or set()
    lines = ['# 记录数据', '']
    for k, v in data.items():
        if k in ('createdAt', 'updatedAt', '_version', '_branchId'):
            continue
        if k in exclude:
            continue
        label = labels.get(k, k)
        lines.append(f'- **{label}**: {v}')
    return '\n'.join(lines) + '\n'


def _copy_attachments(data, file_fields, dest_dir):
    ids = []
    for fn in file_fields:
        val = data.get(fn)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and item.get('uid'):
                    ids.append(item['uid'])
    if not ids:
        return
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, original_name, storage_path FROM data_files "
                        "WHERE id = ANY(%s)", (ids,))
            rows = cur.fetchall()
    att = Path(dest_dir) / 'attachments'
    for _id, name, path in rows:
        src = Path(path)
        if src.exists():
            att.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(att / f"{_id}_{name}"))


def build_context_dir(task, record):
    """Stage <ws_root>/scan-staging/<task>/<record>/ with record.md + attachments/.
    Returns the path RELATIVE to the workspace root (for batch_input_file)."""
    rel = os.path.join('scan-staging', task['id'], record['id'])
    dest = Path(_workspace_root()) / rel
    if dest.exists():
        shutil.rmtree(str(dest))
    dest.mkdir(parents=True, exist_ok=True)
    labels = _field_labels(task['collection'])
    exclude = {task['status_field']} | {m['column'] for m in (task.get('field_mapping') or [])}
    (dest / 'record.md').write_text(
        _render_record_md(record.get('data') or {}, labels, exclude), encoding='utf-8')
    _copy_attachments(record.get('data') or {}, _file_field_names(task['collection']), str(dest))
    return rel


def _pending_predicate(task, params):
    sf = task['status_field']
    pv = task.get('pending_value') or ''
    if pv == '':
        return "(d.data->>%s IS NULL OR d.data->>%s = '')", params + [sf, sf]
    return "d.data->>%s = %s", params + [sf, pv]


def claim_records(task):
    """Atomically pick up to max_records_per_scan pending records and flip them to
    running_value. Returns [{id, data}]."""
    base_params = [task['collection'], task['branch_id']]
    pred, params = _pending_predicate(task, base_params)
    filter_sql = ''
    extra = task.get('extra_filter') or {}
    if extra:
        # mongo_query.translate(query) -> (where_fragment, params); the fragment
        # references the JSONB column unaliased as `data`, which resolves against
        # `dynamic_data d` (single-table FROM). Mirror routes/dynamic.py usage.
        from utils.mongo_query import translate, MongoQueryError
        try:
            clause, fparams = translate(extra)
            if clause and clause != 'TRUE':
                filter_sql = ' AND (' + clause + ')'
                params = params + list(fparams)
        except MongoQueryError:
            filter_sql = ''
    sql = (
        "WITH picked AS ("
        "  SELECT d.id FROM dynamic_data d "
        "   WHERE d.collection = %s AND d.branch_id = %s AND " + pred + filter_sql +
        "   ORDER BY d.created_at FOR UPDATE SKIP LOCKED LIMIT %s) "
        "UPDATE dynamic_data d SET data = jsonb_set(d.data, ARRAY[%s], to_jsonb(%s::text)), "
        "  updated_at = now(), version = d.version + 1 "
        "FROM picked WHERE d.id = picked.id AND d.branch_id = %s "
        "RETURNING d.id, d.data"
    )
    params = params + [task['max_records_per_scan'], task['status_field'],
                       task['running_value'], task['branch_id']]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.commit()
    return [{'id': r[0], 'data': r[1]} for r in rows]


def run_task(task):
    """One scan: claim → stage context → create batch → record run."""
    claimed = claim_records(task)
    if not claimed:
        mark_run(task['id'], 0)
        return
    files = []
    try:
        for rec in claimed:
            rel = build_context_dir(task, rec)
            files.append({'name': rec['id'], 'path': rel, 'recordId': rec['id']})
        prompt = assemble_prompt(task)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        create_batch(task['owner_user_id'], name=f"AI定时·{task['name']}·{stamp}",
                     prompt=prompt, template_id=None, files=files,
                     scan_task_id=task['id'])
        mark_run(task['id'], len(claimed))
    except Exception as e:
        # revert claimed records to pending so they retry next scan
        _revert_claimed(task, [r['id'] for r in claimed])
        mark_run(task['id'], 0, error=f'{type(e).__name__}: {e}'[:500])
        raise


def _revert_claimed(task, record_ids):
    if not record_ids:
        return
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE dynamic_data SET data = jsonb_set(data, ARRAY[%s], to_jsonb(%s::text)) "
                "WHERE id = ANY(%s) AND collection = %s AND branch_id = %s",
                (task['status_field'], task.get('pending_value') or '', record_ids,
                 task['collection'], task['branch_id']),
            )
        conn.commit()


def sweep_orphans():
    """Reset running_value records that have no live (pending/running) child session."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, collection, branch_id, status_field, running_value, "
                    "pending_value FROM ai_scan_tasks")
        tasks = cur.fetchall()
    for tid, coll, branch, sf, running, pending in tasks:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE dynamic_data d SET data = jsonb_set(d.data, ARRAY[%s], to_jsonb(%s::text)) "
                    "WHERE d.collection = %s AND d.branch_id = %s AND d.data->>%s = %s "
                    "AND NOT EXISTS (SELECT 1 FROM ai_chat_sessions s "
                    "  WHERE s.scan_task_id = %s AND s.source_record_id = d.id "
                    "    AND s.status IN ('pending','running'))",
                    (sf, pending or '', coll, branch, sf, running, tid),
                )
            conn.commit()
