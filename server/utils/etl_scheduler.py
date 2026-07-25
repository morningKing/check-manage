"""ETL 任务后台调度器：单线程轮询 etl_logs 里的 pending 行，异步执行。

见 utils/etl_engine.py::execute_task。跑一次 ETL 任务可能是几万到几十万次
数据库写入，不能再放在 HTTP 请求线程里同步跑（会拖住请求线程、占着连接池
里的连接不放，浏览器超时后后端还在傻跑）。routes/etl_tasks.py::run_task
现在只插入一条 status='pending' 的 etl_logs 行就立即返回，真正的执行在这里。

用 APScheduler 的 interval job（与 field_index_scheduler.py / ai_scan_scheduler.py
同一模式）而不是自己起 threading.Thread：max_instances=1 + coalesce=True 保证
同一时刻只有一个 tick 在跑，一个 tick 里的 ETL 任务本身可能耗时很久，跑完才
轮到下一个 tick，天然实现"单线程串行执行多个 ETL 运行请求"，不需要自己管理
线程池/唤醒事件。
"""
import traceback
from datetime import datetime, timezone

import psycopg2.extras
from apscheduler.schedulers.background import BackgroundScheduler

from db import get_db

_scheduler = None
TICK_INTERVAL_SEC = 2


def _claim_one():
    """认领一条 pending 的 etl_logs 行，置为 running，返回 (log_id, task_id)。
    没有待跑的行时返回 None。

    FOR UPDATE SKIP LOCKED 是额外的防御——正常情况下 max_instances=1 已经保证
    不会有并发 tick，这里防的是理论上的多进程场景。
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "WITH picked AS ("
            "  SELECT id FROM etl_logs WHERE status = 'pending'"
            "  ORDER BY started_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            ") "
            "UPDATE etl_logs l SET status = 'running' "
            "FROM picked WHERE l.id = picked.id "
            "RETURNING l.id, l.task_id"
        )
        row = cur.fetchone()
    return row


def _make_progress_cb(log_id):
    def _progress_cb(current, total):
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    'UPDATE etl_logs SET progress_current = %s, total_records = %s WHERE id = %s',
                    (current, total, log_id),
                )
        except Exception:
            traceback.print_exc()
    return _progress_cb


def _make_step_cb(log_id):
    def _step_cb(step_name):
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    'UPDATE etl_logs SET current_step_name = %s WHERE id = %s',
                    (step_name, log_id),
                )
        except Exception:
            traceback.print_exc()
    return _step_cb


def _make_cancel_check(log_id):
    def _cancel_check():
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute('SELECT cancel_requested FROM etl_logs WHERE id = %s', (log_id,))
                row = cur.fetchone()
                return bool(row and row[0])
        except Exception:
            traceback.print_exc()
            return False
    return _cancel_check


def _run_one(log_id, task_id):
    from utils.etl_engine import execute_task
    # 延迟到调用时才取 db.pool：本模块可能在 db.get_pool() 第一次被调用（也就
    # 是 db.pool 从 None 变成真实连接池实例）之前就被导入（例如 pytest 收集
    # 测试模块时），若在模块顶层 `from db import pool`，绑定的会是当时那个
    # None，之后 db.py 里 pool 被重新赋值也不会反映到这里。同一手法见
    # utils/backup.py 里的 `from db import pool` 局部导入。
    from db import pool

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, description, steps FROM etl_tasks WHERE id = %s', (task_id,))
        row = cur.fetchone()
    if not row:
        # 认领之后、执行之前任务被删除了
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE etl_logs SET status = 'error', error_detail = %s, finished_at = NOW() WHERE id = %s",
                ('任务已被删除', log_id),
            )
        return

    task = {'id': row[0], 'name': row[1], 'description': row[2], 'steps': row[3] or []}

    exec_conn = pool.getconn()
    try:
        context = execute_task(
            task, exec_conn, dry_run=False,
            step_cb=_make_step_cb(log_id),
            progress_cb=_make_progress_cb(log_id),
            cancel_check=_make_cancel_check(log_id),
        )
        finished_at = datetime.now(timezone.utc)

        if context.get('cancelled'):
            status = 'cancelled'
        elif context['error'] > 0 and context['success'] > 0:
            status = 'partial'
        elif context['error'] > 0 and context['success'] == 0:
            status = 'error'
        else:
            status = 'success'

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'UPDATE etl_logs SET status = %s, finished_at = %s, total_records = %s, '
                'success_count = %s, error_count = %s, step_results = %s, error_detail = %s, '
                'progress_current = %s '
                'WHERE id = %s',
                (status, finished_at, context['total'], context['success'], context['error'],
                 psycopg2.extras.Json(context['step_results']),
                 '\n'.join(context['errors']) if context['errors'] else None,
                 context['success'] + context['error'], log_id),
            )
            cur.execute(
                'UPDATE etl_tasks SET last_run_at = %s, last_run_status = %s WHERE id = %s',
                (finished_at, status, task_id),
            )
    except Exception as e:
        exec_conn.rollback()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE etl_logs SET status = 'error', finished_at = NOW(), error_detail = %s WHERE id = %s",
                (str(e)[:2000], log_id),
            )
    finally:
        pool.putconn(exec_conn)


def _tick():
    claimed = _claim_one()
    if not claimed:
        return
    log_id, task_id = claimed
    _run_one(log_id, task_id)


def _safe_tick():
    try:
        _tick()
    except Exception:
        traceback.print_exc()


def _restart_audit():
    """进程重启时把遗留的 running 行重置回 pending，避免永远卡住。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE etl_logs SET status = 'pending' WHERE status = 'running'")


def start_etl_scheduler(app):
    global _scheduler
    if _scheduler is not None:
        return
    try:
        _restart_audit()
    except Exception:
        traceback.print_exc()
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_safe_tick, 'interval', seconds=TICK_INTERVAL_SEC, id='etl_tick',
                       max_instances=1, coalesce=True)
    _scheduler.start()
