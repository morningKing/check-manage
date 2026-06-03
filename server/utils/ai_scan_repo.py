import uuid
import psycopg2.extras
from db import get_db

_FIELDS = ('id', 'name', 'enabled', 'owner_user_id', 'collection', 'branch_id',
           'status_field', 'pending_value', 'running_value', 'done_value',
           'failed_value', 'extra_filter', 'context_fields', 'prompt_template',
           'field_mapping', 'schedule_interval_minutes', 'max_records_per_scan',
           'last_run_at', 'last_scan_count', 'last_error', 'created_at', 'updated_at')


def _row_to_dict(r):
    d = dict(zip(_FIELDS, r))
    for ts in ('last_run_at', 'created_at', 'updated_at'):
        if d.get(ts) is not None:
            d[ts] = d[ts].isoformat()
    return d


def list_tasks():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(_FIELDS)} FROM ai_scan_tasks ORDER BY created_at DESC")
        return [_row_to_dict(r) for r in cur.fetchall()]


def get_task(task_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(_FIELDS)} FROM ai_scan_tasks WHERE id = %s", (task_id,))
        r = cur.fetchone()
        return _row_to_dict(r) if r else None


def create_task(body, owner_user_id):
    tid = f"scan-{uuid.uuid4().hex[:8]}"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_scan_tasks (id, name, enabled, owner_user_id, collection, "
            "branch_id, status_field, pending_value, running_value, done_value, failed_value, "
            "extra_filter, context_fields, prompt_template, field_mapping, "
            "schedule_interval_minutes, max_records_per_scan) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tid, body['name'], body.get('enabled', True), owner_user_id, body['collection'],
             body.get('branchId', 'main'), body['statusField'], body.get('pendingValue', ''),
             body.get('runningValue', '处理中'), body.get('doneValue', '已处理'),
             body.get('failedValue', '处理失败'),
             psycopg2.extras.Json(body.get('extraFilter') or {}),
             psycopg2.extras.Json(body.get('contextFields') or {}),
             body['promptTemplate'], psycopg2.extras.Json(body.get('fieldMapping') or []),
             int(body.get('scheduleIntervalMinutes', 15)), int(body.get('maxRecordsPerScan', 20))),
        )
    return get_task(tid)


_UPDATABLE = {
    'name': 'name', 'enabled': 'enabled', 'collection': 'collection', 'branchId': 'branch_id',
    'statusField': 'status_field', 'pendingValue': 'pending_value', 'runningValue': 'running_value',
    'doneValue': 'done_value', 'failedValue': 'failed_value', 'promptTemplate': 'prompt_template',
    'scheduleIntervalMinutes': 'schedule_interval_minutes', 'maxRecordsPerScan': 'max_records_per_scan',
}
_UPDATABLE_JSON = {'extraFilter': 'extra_filter', 'contextFields': 'context_fields', 'fieldMapping': 'field_mapping'}


def update_task(task_id, body):
    sets, params = [], []
    for k, col in _UPDATABLE.items():
        if k in body:
            sets.append(f"{col} = %s"); params.append(body[k])
    for k, col in _UPDATABLE_JSON.items():
        if k in body:
            sets.append(f"{col} = %s"); params.append(psycopg2.extras.Json(body[k]))
    if not sets:
        return get_task(task_id)
    sets.append("updated_at = now()")
    params.append(task_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE ai_scan_tasks SET {', '.join(sets)} WHERE id = %s", params)
    return get_task(task_id)


def delete_task(task_id):
    """Reset this task's in-flight (running_value) records to pending_value, then delete."""
    t = get_task(task_id)
    if not t:
        return False
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dynamic_data SET data = jsonb_set(data, ARRAY[%s], to_jsonb(%s::text)) "
            "WHERE collection = %s AND branch_id = %s AND data->>%s = %s",
            (t['status_field'], t['pending_value'], t['collection'], t['branch_id'],
             t['status_field'], t['running_value']),
        )
        cur.execute("DELETE FROM ai_scan_tasks WHERE id = %s", (task_id,))
    return True


def mark_run(task_id, scan_count, error=None):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_scan_tasks SET last_run_at = now(), last_scan_count = %s, "
                    "last_error = %s WHERE id = %s", (scan_count, error, task_id))
