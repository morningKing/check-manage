"""
备份与还原核心逻辑 + 定时备份调度器

职责：
- 导出全部业务表数据为 JSON，打包 ZIP
- 从 ZIP 还原数据（单事务，失败回滚）
- 后台线程轮询定时备份设置
"""

import os
import json
import uuid
import time
import zipfile
import threading
from datetime import datetime, timezone, timedelta
from db import get_db
import psycopg2.extras

# 备份文件存储目录
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backups')

# 需要备份的业务表及其列定义
# (表名, 列列表, JSONB列索引集合)
BACKUP_TABLES = [
    ('menus', ['id', 'name', 'icon', 'page_id', 'parent_id', '"order"', 'path', 'roles'], {7}),
    ('page_configs', ['id', 'name', 'description', 'api_endpoint', 'fields', 'created_at', 'updated_at',
                      'export_scripts', 'row_export_scripts', 'api_public', 'validation_script'], {4, 7, 8}),
    ('dynamic_data', ['id', 'collection', 'data', 'created_at', 'updated_at', 'version'], {2}),
    ('data_relations', ['collection', 'record_id', 'field_name', 'related_collection', 'related_id'], set()),
    ('users', ['id', 'username', 'password_hash', 'display_name', 'role', 'created_at'], set()),
    ('operation_logs', ['id', 'action', 'target_type', 'target_id', 'target_name', 'description',
                        'operator_id', 'operator_name', 'operator_role', 'created_at',
                        'batch_id', 'batch_desc'], set()),
    ('export_scripts', ['id', 'name', 'description', 'language', 'script', 'output_format',
                        'created_at', 'updated_at', 'scope'], set()),
    ('api_keys', ['id', 'name', 'key_hash', 'created_at', 'last_used_at', 'is_active'], set()),
    ('validation_scripts', ['id', 'name', 'description', 'script', 'created_at', 'updated_at'], set()),
    ('etl_tasks', ['id', 'name', 'description', 'steps', 'enabled', 'last_run_at', 'last_run_status',
                   'created_at', 'updated_at'], {3}),
    ('etl_logs', ['id', 'task_id', 'task_name', 'status', 'started_at', 'finished_at',
                  'total_records', 'success_count', 'error_count', 'step_results', 'error_detail',
                  'created_at'], {9}),
]

# 备份版本号（用于未来兼容性迁移）
BACKUP_VERSION = 1


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _serialize_value(val):
    """将数据库值序列化为 JSON 可存储的格式"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, (dict, list)):
        return val
    return val


def _export_table(cur, table_name, columns):
    """导出单张表的所有数据"""
    col_str = ', '.join(columns)
    cur.execute(f'SELECT {col_str} FROM {table_name}')
    rows = cur.fetchall()
    # 用不带引号的列名作为 key
    clean_cols = [c.strip('"') for c in columns]
    records = []
    for row in rows:
        record = {}
        for i, col in enumerate(clean_cols):
            record[col] = _serialize_value(row[i])
        records.append(record)
    return records


def create_backup(backup_type='manual', created_by=None):
    """
    创建备份

    导出 7 张业务表 → JSON → ZIP，保存到 server/backups/
    返回备份元数据 dict
    """
    _ensure_backup_dir()

    backup_id = f'backup-{uuid.uuid4().hex[:12]}'
    now = datetime.now(timezone.utc)
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    type_label = '手动备份' if backup_type == 'manual' else '定时备份'
    backup_name = f'{type_label} {now_str}'

    table_stats = {}
    total_records = 0

    # 1. 导出所有表数据
    with get_db() as conn:
        cur = conn.cursor()
        table_data = {}
        for table_name, columns, _ in BACKUP_TABLES:
            records = _export_table(cur, table_name, columns)
            table_data[table_name] = records
            table_stats[table_name] = len(records)
            total_records += len(records)

    # 2. 构建 manifest
    manifest = {
        'version': BACKUP_VERSION,
        'id': backup_id,
        'name': backup_name,
        'type': backup_type,
        'createdAt': now.isoformat(),
        'createdBy': created_by,
        'tables': table_stats,
        'totalRecords': total_records,
    }

    # 3. 打包 ZIP
    zip_filename = f'{backup_id}.zip'
    zip_path = os.path.join(BACKUP_DIR, zip_filename)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        for table_name, records in table_data.items():
            zf.writestr(f'{table_name}.json', json.dumps(records, ensure_ascii=False, indent=2))

    file_size = os.path.getsize(zip_path)

    # 4. 写入备份记录
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO backups (id, name, type, status, file_path, file_size, '
            'tables_count, records_count, created_by, created_at, note) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (backup_id, backup_name, backup_type, 'completed', zip_path,
             file_size, len(BACKUP_TABLES), total_records, created_by, now, None),
        )

    # 5. 如果是定时备份，更新 last_backup_at
    if backup_type == 'scheduled':
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    'UPDATE backup_settings SET last_backup_at = %s, updated_at = %s WHERE id = 1',
                    (now, now),
                )
        except Exception:
            pass

    return {
        'id': backup_id,
        'name': backup_name,
        'type': backup_type,
        'status': 'completed',
        'filePath': zip_path,
        'fileSize': file_size,
        'tablesCount': len(BACKUP_TABLES),
        'recordsCount': total_records,
        'createdBy': created_by,
        'createdAt': now.isoformat(),
        'note': None,
    }


def restore_backup(zip_path):
    """
    从 ZIP 备份文件还原数据

    在单个事务中 TRUNCATE 全部业务表并重新 INSERT。
    不清空 backups 和 backup_settings 表。
    失败则全部回滚。
    """
    # 1. 解压并校验
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f'备份文件不存在: {zip_path}')

    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = zf.namelist()
        if 'manifest.json' not in names:
            raise ValueError('无效的备份文件：缺少 manifest.json')

        manifest = json.loads(zf.read('manifest.json'))
        table_data = {}
        for table_name, _, _ in BACKUP_TABLES:
            json_file = f'{table_name}.json'
            if json_file in names:
                table_data[table_name] = json.loads(zf.read(json_file))

    # 2. 在单个事务中还原
    conn = None
    try:
        from db import pool
        conn = pool.getconn()
        cur = conn.cursor()

        # TRUNCATE 所有业务表（反序以避免潜在依赖）
        table_names = [t[0] for t in BACKUP_TABLES]
        for table_name in reversed(table_names):
            cur.execute(f'TRUNCATE TABLE {table_name} CASCADE')

        # INSERT 数据
        for table_name, columns, jsonb_indices in BACKUP_TABLES:
            records = table_data.get(table_name, [])
            clean_cols = [c.strip('"') for c in columns]
            col_str = ', '.join(columns)
            placeholders = ', '.join(['%s'] * len(columns))

            for record in records:
                values = []
                for i, col in enumerate(clean_cols):
                    val = record.get(col)
                    if i in jsonb_indices and val is not None:
                        val = psycopg2.extras.Json(val)
                    values.append(val)
                cur.execute(
                    f'INSERT INTO {table_name} ({col_str}) VALUES ({placeholders})',
                    values,
                )

        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            from db import pool
            pool.putconn(conn)

    return manifest


def delete_backup_file(file_path):
    """删除磁盘上的备份文件"""
    try:
        if file_path and os.path.isfile(file_path):
            os.remove(file_path)
    except OSError:
        pass


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
