import threading
import traceback
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler

_scheduler = None
_locks = {}
_locks_guard = threading.Lock()


def _is_due(task, now):
    if not task.get('enabled', True):
        return False
    lr = task.get('lastRunAt')
    if not lr:
        return True
    if isinstance(lr, str):
        lr = datetime.fromisoformat(lr)
    interval = task.get('scheduleIntervalMinutes', 15)
    return (now - lr).total_seconds() >= interval * 60


def _task_lock(task_id):
    with _locks_guard:
        return _locks.setdefault(task_id, threading.Lock())


def _tick():
    # P1 §5.8：scan scheduler 纳入 DB 租约体系——多进程部署时只有一个实例
    # 真正执行扫描 tick；抢不到（他实例持有）则静默跳过本轮（下一分钟重试）。
    try:
        from utils import execution_lease
        owner = execution_lease.owner_id()
        ok, _ = execution_lease.acquire('scan_scheduler', owner,
                                        lease_kind='scan_scheduler')
        if not ok:
            return
    except Exception:
        pass  # 租约基建不可用 → 退化为单实例内 APScheduler 互斥，不阻断扫描
    from utils.ai_scan_repo import list_tasks
    from utils.ai_scan_engine import run_task
    now = datetime.now(timezone.utc)
    for task in list_tasks():
        if not task.get('enabled', True) or not _is_due(task, now):
            continue
        lock = _task_lock(task['id'])
        if not lock.acquire(blocking=False):
            continue
        try:
            run_task(task)
        except Exception:
            traceback.print_exc()
        finally:
            lock.release()


def start_scan_scheduler(app):
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from utils.ai_scan_engine import sweep_orphans
        sweep_orphans()
    except Exception:
        traceback.print_exc()
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_tick, 'interval', minutes=1, id='ai_scan_tick',
                       max_instances=1, coalesce=True)
    _scheduler.start()
