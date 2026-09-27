"""statusBadge 字段超时兜底定时任务。

每分钟扫描一次所有配置了 statusBadge 且设置了 timeoutSec 的字段：找到当前值
非终态、且自己的变化时间戳早于 (now - timeoutSec) 的记录，直接把该字段写成
配置的 timeoutValue 并刷新时间戳。这是后端对 dynamic_data 的直接 UPDATE，不
经过 dynamic.py/open_api.py 的路由逻辑，因此不会触发 webhook（避免第三方系统
收到自己造成的"超时"这个回环通知）。
"""
import traceback
from datetime import datetime, timezone
import psycopg2.extras
from apscheduler.schedulers.background import BackgroundScheduler
from db import get_db

_scheduler = None


def _tick():
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute('SELECT id, fields FROM page_configs')
        page_rows = cur.fetchall()

        for page_row in page_rows:
            page_id = page_row['id']
            fields = page_row['fields']
            if not page_id.startswith('page-') or not fields:
                continue
            collection = page_id[len('page-'):]

            for f in fields:
                if f.get('controlType') != 'statusBadge':
                    continue
                cfg = f.get('statusBadgeConfig') or {}
                timeout_sec = cfg.get('timeoutSec')
                timeout_value = cfg.get('timeoutValue')
                field_name = f.get('fieldName')
                if not timeout_sec or not timeout_value or not field_name:
                    continue

                terminal_values = [o['value'] for o in cfg.get('options', []) if o.get('terminal')]
                ts_key = f'_statusBadge_{field_name}_changedAt'

                cur.execute(
                    'SELECT id, branch_id, data FROM dynamic_data '
                    'WHERE collection = %s '
                    "AND data->>%s IS NOT NULL "
                    'AND NOT (data->>%s = ANY(%s)) '
                    "AND (data->>%s)::timestamptz < NOW() - (%s * INTERVAL '1 second')",
                    (collection, field_name, field_name, terminal_values, ts_key, timeout_sec),
                )
                timed_out = cur.fetchall()
                for row in timed_out:
                    data = dict(row['data'] or {})
                    data[field_name] = timeout_value
                    data[ts_key] = now_iso
                    cur.execute(
                        'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 '
                        'WHERE collection = %s AND id = %s AND branch_id = %s',
                        (psycopg2.extras.Json(data), collection, row['id'], row['branch_id']),
                    )
        conn.commit()


def _safe_tick():
    try:
        _tick()
    except Exception:
        traceback.print_exc()


def start_status_badge_timeout_scheduler(app):
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_safe_tick, 'interval', minutes=1, id='status_badge_timeout_tick',
                       max_instances=1, coalesce=True)
    _scheduler.start()
