import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from db import get_db

from .service import create_backup, delete_backup_file


logger = logging.getLogger(__name__)



def get_backup_settings():
    """获取定时备份设置"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT enabled, interval, retention_count, last_backup_at, updated_at '
                    'FROM backup_settings WHERE id = 1')
        row = cur.fetchone()
        if not row:
            return {
                'enabled': False,
                'interval': 'daily',
                'retentionCount': 10,
                'lastBackupAt': None,
                'updatedAt': None,
            }
        return {
            'enabled': row[0],
            'interval': row[1],
            'retentionCount': row[2],
            'lastBackupAt': row[3].isoformat() if row[3] else None,
            'updatedAt': row[4].isoformat() if row[4] else None,
        }


def update_backup_settings(enabled, interval, retention_count):
    """更新定时备份设置"""
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE backup_settings SET enabled = %s, interval = %s, '
            'retention_count = %s, updated_at = %s WHERE id = 1',
            (enabled, interval, retention_count, now),
        )
    return get_backup_settings()


def is_backup_due(settings):
    """判断是否到了执行定时备份的时间"""
    last = settings.get('lastBackupAt')
    if not last:
        return True

    if isinstance(last, str):
        try:
            last_dt = datetime.fromisoformat(last)
        except ValueError:
            return True
    else:
        last_dt = last

    # 确保 timezone-aware
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    interval = settings.get('interval', 'daily')

    if interval == 'daily':
        return (now - last_dt) >= timedelta(hours=24)
    elif interval == 'weekly':
        return (now - last_dt) >= timedelta(days=7)
    elif interval == 'monthly':
        return (now - last_dt) >= timedelta(days=30)
    return False


def cleanup_old_backups(retention_count):
    """清理超出保留数量的旧定时备份"""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, file_path FROM backups WHERE type = 'scheduled' "
                "ORDER BY created_at DESC"
            )
            rows = cur.fetchall()
            if len(rows) <= retention_count:
                return
            to_delete = rows[retention_count:]
            for backup_id, file_path in to_delete:
                delete_backup_file(file_path)
                cur.execute('DELETE FROM backups WHERE id = %s', (backup_id,))
    except Exception:
        pass


def start_backup_scheduler(app):
    """启动备份调度器后台线程"""
    # P2-B2：统一租约框架——抢不到 backup scheduler 租约就不在本进程启动
    # （多实例部署只有一份定时备份在跑；本批不做后台重试，简单 return）。
    from ai.batch import execution_lease as execution_lease
    ok, _ = execution_lease.acquire('backup', execution_lease.owner_id(),
                                    lease_kind='scheduler')
    if not ok:
        logger.warning('backup scheduler lease NOT acquired; '
                       'disabled in this process')
        return

    def scheduler_loop():
        while True:
            time.sleep(60)  # 每分钟检查一次
            try:
                with app.app_context():
                    settings = get_backup_settings()
                    if not settings['enabled']:
                        continue
                    if is_backup_due(settings):
                        create_backup(backup_type='scheduled', created_by='系统定时')
                        cleanup_old_backups(settings['retentionCount'])
            except Exception:
                pass

    thread = threading.Thread(target=scheduler_loop, daemon=True)
    thread.start()



__all__ = [
    'logger',
    'datetime',
    'timezone',
    'timedelta',
    'get_db',
    'get_backup_settings',
    'update_backup_settings',
    'is_backup_due',
    'cleanup_old_backups',
    'start_backup_scheduler',
]
