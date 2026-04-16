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
# (表名, 列列表, JSONB列索引集合, 中文标签)
BACKUP_TABLES = [
    ('menus', ['id', 'name', 'icon', 'page_id', 'parent_id', '"order"', 'path', 'roles'], {7}, '菜单配置'),
    ('page_configs', ['id', 'name', 'description', 'api_endpoint', 'fields', 'created_at', 'updated_at',
                      'export_scripts', 'row_export_scripts', 'api_public', 'validation_script',
                      'api_writable', 'view_config', 'delete_binding'], {4, 7, 8, 12, 13}, '页面配置'),
    ('dynamic_data', ['id', 'collection', 'data', 'created_at', 'updated_at', 'version', 'branch_id'], {2}, '动态数据'),
    ('data_relations', ['collection', 'record_id', 'field_name', 'related_collection', 'related_id', 'branch_id'], set(), '数据关联'),
    ('users', ['id', 'username', 'password_hash', 'display_name', 'role', 'created_at'], set(), '用户数据'),
    ('operation_logs', ['id', 'action', 'target_type', 'target_id', 'target_name', 'description',
                        'operator_id', 'operator_name', 'operator_role', 'created_at',
                        'batch_id', 'batch_desc', 'branch_id'], set(), '操作日志'),
    ('export_scripts', ['id', 'name', 'description', 'language', 'script', 'output_format',
                        'created_at', 'updated_at', 'scope'], set(), '导出脚本'),
    ('api_keys', ['id', 'name', 'key_hash', 'created_at', 'last_used_at', 'is_active'], set(), 'API密钥'),
    ('validation_scripts', ['id', 'name', 'description', 'script', 'created_at', 'updated_at'], set(), '校验脚本'),
    ('etl_tasks', ['id', 'name', 'description', 'steps', 'enabled', 'last_run_at', 'last_run_status',
                   'created_at', 'updated_at'], {3}, 'ETL任务'),
    ('etl_logs', ['id', 'task_id', 'task_name', 'status', 'started_at', 'finished_at',
                  'total_records', 'success_count', 'error_count', 'step_results', 'error_detail',
                  'created_at'], {9}, 'ETL日志'),
    # 版本管理相关表
    ('collection_versions', ['id', 'collection', 'name', 'description', 'version_type', 'parent_version',
                             'status', 'data_hash', 'records_count', 'relations_count', 'created_by',
                             'created_at', 'merged_at', 'merged_by', 'merged_into', 'is_protected'], set(), '版本元数据'),
    ('version_snapshots', ['version_id', 'record_id', 'record_data', 'created_at'], {2}, '版本快照'),
    ('version_relations', ['version_id', 'collection', 'record_id', 'field_name', 'related_collection', 'related_id'], set(), '版本关联'),
    ('user_current_branch', ['id', 'user_id', 'username', 'collection', 'branch_id', 'updated_at'], set(), '用户分支映射'),
]

# 表名到定义的映射
BACKUP_TABLE_MAP = {t[0]: t for t in BACKUP_TABLES}

# 备份版本号（用于未来兼容性迁移）
BACKUP_VERSION = 1

# 还原时的表导入顺序（遵循外键依赖关系）
# 被依赖的表排在前面，依赖其他表的表排在后面
RESTORE_ORDER = [
    # Level 1: No or few dependencies
    'users',
    'page_configs',
    'export_scripts',
    'validation_scripts',
    'api_keys',
    'etl_tasks',
    'dynamic_data',
    # Level 2: Self-referencing or simple dependencies
    'collection_versions',  # Self-referencing, but parent to many
    'menus',                # Depends on page_configs
    'etl_logs',             # Depends on etl_tasks
    # Level 3: Multiple dependencies
    'data_relations',       # Depends on dynamic_data
    'operation_logs',       # Depends on users, dynamic_data
    'user_current_branch',  # Depends on users, collection_versions
    'version_snapshots',    # Depends on collection_versions
    'version_relations',    # Depends on collection_versions
]


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


def _export_table(cur, table_name, columns, collection_filter=None):
    """导出单张表的所有数据

    Parameters
    ----------
    cur : cursor
        数据库游标
    table_name : str
        表名
    columns : list
        列列表
    collection_filter : str | None
        对于 dynamic_data 表，可选的 collection 过滤

    Returns
    -------
    list[dict]
        记录列表
    """
    col_str = ', '.join(columns)
    if table_name == 'dynamic_data' and collection_filter:
        cur.execute(f'SELECT {col_str} FROM {table_name} WHERE collection = %s', (collection_filter,))
    else:
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


def get_backup_table_names():
    """获取可备份的表名列表"""
    return [
        {'name': t[0], 'label': t[3]}
        for t in BACKUP_TABLES
    ]


def create_backup(backup_type='manual', created_by=None, tables=None):
    """
    创建备份

    导出业务表 → JSON → ZIP，保存到 server/backups/

    Parameters
    ----------
    backup_type : str
        'manual' 或 'scheduled'
    created_by : str
        创建者用户名
    tables : list[str] | None
        None = 全量备份（所有 BACKUP_TABLES）
        ['table1', 'table2'] = 表级备份（只备份指定表）
        ['dynamic_data:collection1', 'dynamic_data:collection2'] = 只备份指定 collection

    Returns
    -------
    dict
        备份元数据
    """
    _ensure_backup_dir()

    # 解析要备份的内容
    # tables_to_backup: 实际要备份的表名列表
    # collection_filters: {table_name: [collection1, collection2]} 过滤条件
    # original_tables: 原始表名列表（保留 dynamic_data:collection 格式，仅包含有效的表）
    collection_filters = {}
    tables_to_backup = []
    original_tables = []  # 保存有效的原始表名用于记录
    backup_scope = 'full'

    if tables:
        backup_scope = 'partial'
        for t in tables:
            if ':' in t:
                # 格式：dynamic_data:collection_name
                base_table, collection = t.split(':', 1)
                if base_table in BACKUP_TABLE_MAP:
                    if base_table not in collection_filters:
                        collection_filters[base_table] = []
                        tables_to_backup.append(base_table)
                    collection_filters[base_table].append(collection)
                    original_tables.append(t)  # 保留有效的原始表名
            elif t in BACKUP_TABLE_MAP:
                tables_to_backup.append(t)
                original_tables.append(t)  # 保留有效的原始表名

        # 去重
        tables_to_backup = list(dict.fromkeys(tables_to_backup))
    else:
        tables_to_backup = [t[0] for t in BACKUP_TABLES]

    if not tables_to_backup:
        raise ValueError('没有有效的表需要备份')

    backup_id = f'backup-{uuid.uuid4().hex[:12]}'
    now = datetime.now(timezone.utc)
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    type_label = '手动备份' if backup_type == 'manual' else '定时备份'
    scope_label = '全量' if backup_scope == 'full' else '表级'
    backup_name = f'{type_label}({scope_label}) {now_str}'

    table_stats = {}
    total_records = 0

    # 1. 导出指定表数据
    with get_db() as conn:
        cur = conn.cursor()
        table_data = {}
        for table_name in tables_to_backup:
            _, columns, _, _ = BACKUP_TABLE_MAP[table_name]

            # 检查是否有 collection 过滤
            if table_name in collection_filters:
                # 按 collection 分别导出
                records = []
                for col in collection_filters[table_name]:
                    col_records = _export_table(cur, table_name, columns, collection_filter=col)
                    records.extend(col_records)
                table_data[table_name] = records
                table_stats[table_name] = len(records)
            else:
                records = _export_table(cur, table_name, columns)
                table_data[table_name] = records
                table_stats[table_name] = len(records)
            total_records += len(records)

        # 如果备份了 dynamic_data，需要同步备份对应的 data_relations
        if 'dynamic_data' in tables_to_backup and 'data_relations' not in table_data:
            _, rel_columns, _, _ = BACKUP_TABLE_MAP['data_relations']
            # 如果有 collection 过滤，只备份对应的关联数据
            if 'dynamic_data' in collection_filters:
                rel_records = []
                for col in collection_filters['dynamic_data']:
                    cur.execute(
                        'SELECT collection, record_id, field_name, related_collection, related_id, branch_id '
                        'FROM data_relations WHERE collection = %s',
                        (col,)
                    )
                    for row in cur.fetchall():
                        rel_records.append({
                            'collection': row[0],
                            'record_id': row[1],
                            'field_name': row[2],
                            'related_collection': row[3],
                            'related_id': row[4],
                            'branch_id': row[5],
                        })
                table_data['data_relations'] = rel_records
                table_stats['data_relations'] = len(rel_records)
                total_records += len(rel_records)
            else:
                table_data['data_relations'] = _export_table(cur, 'data_relations', rel_columns)
                table_stats['data_relations'] = len(table_data['data_relations'])
                total_records += table_stats['data_relations']

        # 如果备份了 dynamic_data，需要同步备份对应的 version_snapshots 和 version_relations
        if 'dynamic_data' in tables_to_backup and 'version_snapshots' not in table_data:
            _, vs_columns, _, _ = BACKUP_TABLE_MAP['version_snapshots']
            if 'dynamic_data' in collection_filters:
                # 按 collection 过滤版本快照
                vs_records = []
                for col in collection_filters['dynamic_data']:
                    cur.execute(
                        'SELECT vs.version_id, vs.record_id, vs.record_data, vs.created_at '
                        'FROM version_snapshots vs '
                        'JOIN collection_versions cv ON vs.version_id = cv.id '
                        'WHERE cv.collection = %s',
                        (col,)
                    )
                    for row in cur.fetchall():
                        vs_records.append({
                            'version_id': row[0],
                            'record_id': row[1],
                            'record_data': row[2],
                            'created_at': row[3].isoformat() if row[3] else None,
                        })
                table_data['version_snapshots'] = vs_records
                table_stats['version_snapshots'] = len(vs_records)
                total_records += len(vs_records)
            else:
                table_data['version_snapshots'] = _export_table(cur, 'version_snapshots', vs_columns)
                table_stats['version_snapshots'] = len(table_data['version_snapshots'])
                total_records += table_stats['version_snapshots']

        if 'dynamic_data' in tables_to_backup and 'version_relations' not in table_data:
            _, vr_columns, _, _ = BACKUP_TABLE_MAP['version_relations']
            if 'dynamic_data' in collection_filters:
                vr_records = []
                for col in collection_filters['dynamic_data']:
                    cur.execute(
                        'SELECT vr.version_id, vr.collection, vr.record_id, vr.field_name, '
                        'vr.related_collection, vr.related_id '
                        'FROM version_relations vr '
                        'JOIN collection_versions cv ON vr.version_id = cv.id '
                        'WHERE cv.collection = %s',
                        (col,)
                    )
                    for row in cur.fetchall():
                        vr_records.append({
                            'version_id': row[0],
                            'collection': row[1],
                            'record_id': row[2],
                            'field_name': row[3],
                            'related_collection': row[4],
                            'related_id': row[5],
                        })
                table_data['version_relations'] = vr_records
                table_stats['version_relations'] = len(vr_records)
                total_records += len(vr_records)
            else:
                table_data['version_relations'] = _export_table(cur, 'version_relations', vr_columns)
                table_stats['version_relations'] = len(table_data['version_relations'])
                total_records += table_stats['version_relations']

    # 2. 构建 manifest
    # 对于表级备份，保留原始表名（包含 collection 信息）
    manifest_tables = original_tables if original_tables else tables_to_backup
    manifest = {
        'version': BACKUP_VERSION,
        'id': backup_id,
        'name': backup_name,
        'type': backup_type,
        'scope': backup_scope,
        'tables': manifest_tables,
        'tableStats': table_stats,
        'totalRecords': total_records,
        'createdAt': now.isoformat(),
        'createdBy': created_by,
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
    # 对于表级备份，保留原始表名（包含 collection 信息）
    backup_tables_json = manifest_tables
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO backups (id, name, type, status, file_path, file_size, '
            'tables_count, records_count, created_by, created_at, note, backup_scope, backup_tables) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (backup_id, backup_name, backup_type, 'completed', zip_path,
             file_size, len(tables_to_backup), total_records, created_by, now, None,
             backup_scope, psycopg2.extras.Json(backup_tables_json)),
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
        'tablesCount': len(tables_to_backup),
        'recordsCount': total_records,
        'createdBy': created_by,
        'createdAt': now.isoformat(),
        'note': None,
        'backupScope': backup_scope,
        'backupTables': manifest_tables,
    }


def restore_backup(zip_path, tables=None):
    """
    从 ZIP 备份文件还原数据

    在单个事务中还原数据。
    不清空 backups 和 backup_settings 表。
    失败则全部回滚。

    Parameters
    ----------
    zip_path : str
        ZIP 备份文件路径
    tables : list[str] | None
        None = 还原备份中的所有表
        ['table1'] = 只还原指定表（必须是备份中包含的表）
        ['dynamic_data:collection1'] = 只还原指定 collection 的数据

    Returns
    -------
    dict
        manifest 信息
    """
    # 1. 解压并校验
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f'备份文件不存在: {zip_path}')

    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = zf.namelist()
        if 'manifest.json' not in names:
            raise ValueError('无效的备份文件：缺少 manifest.json')

        manifest = json.loads(zf.read('manifest.json'))

        # 确定备份中包含的表（提取基础表名）
        backup_tables_raw = manifest.get('tables', [])
        if not backup_tables_raw:
            # 旧版备份格式，从文件名推断
            backup_tables_raw = [t[0] for t in BACKUP_TABLES if f'{t[0]}.json' in names]

        # 提取基础表名（去除 collection 部分）
        backup_base_tables = set()
        for t in backup_tables_raw:
            base_name = t.split(':')[0] if ':' in t else t
            backup_base_tables.add(base_name)

        # 解析要还原的内容
        # collection_filters: {table_name: [collection1, collection2]} 过滤条件
        collection_filters = {}
        tables_to_restore_set = set()  # 使用 set 去重

        if tables:
            for t in tables:
                if ':' in t:
                    # 格式：dynamic_data:collection_name
                    base_table, collection = t.split(':', 1)
                    if base_table in backup_base_tables:
                        if base_table not in collection_filters:
                            collection_filters[base_table] = []
                            tables_to_restore_set.add(base_table)
                        collection_filters[base_table].append(collection)
                elif t in backup_base_tables:
                    tables_to_restore_set.add(t)

            if not tables_to_restore_set:
                raise ValueError('指定的表不在备份中')
        else:
            tables_to_restore_set = backup_base_tables

        # 按照 RESTORE_ORDER 的顺序排列表，确保外键依赖正确
        tables_to_restore = [
            t for t in RESTORE_ORDER
            if t in tables_to_restore_set
        ]
        # 添加不在 RESTORE_ORDER 中的表（如有新增表）
        for t in tables_to_restore_set:
            if t not in tables_to_restore:
                tables_to_restore.append(t)

        # 读取表数据
        table_data = {}
        for table_name in backup_base_tables:
            json_file = f'{table_name}.json'
            if json_file in names:
                all_records = json.loads(zf.read(json_file))
                # 如果有 collection 过滤，只保留指定 collection 的记录
                if table_name in collection_filters:
                    filtered = [r for r in all_records if r.get('collection') in collection_filters[table_name]]
                    table_data[table_name] = filtered
                else:
                    table_data[table_name] = all_records
            else:
                table_data[table_name] = []

    # 2. 在单个事务中还原
    conn = None
    main_success = False  # 跟踪主操作是否成功
    try:
        from db import pool
        conn = pool.getconn()
        cur = conn.cursor()

        # 临时禁用触发器以绕过外键约束检查
        # 这允许我们按任意顺序插入数据，包括自引用的情况
        cur.execute('SET session_replication_role = replica')

        # 对于 collection 级别的还原，使用 DELETE 而不是 TRUNCATE
        for table_name in tables_to_restore:
            if table_name in collection_filters:
                # 只删除指定 collection 的数据
                for col in collection_filters[table_name]:
                    cur.execute(f'DELETE FROM {table_name} WHERE collection = %s', (col,))
                    # 删除相关的 data_relations
                    if table_name == 'dynamic_data':
                        cur.execute('DELETE FROM data_relations WHERE collection = %s', (col,))
                        # 删除相关的版本数据
                        cur.execute(
                            'DELETE FROM version_snapshots WHERE version_id IN '
                            '(SELECT id FROM collection_versions WHERE collection = %s)',
                            (col,)
                        )
                        cur.execute(
                            'DELETE FROM version_relations WHERE version_id IN '
                            '(SELECT id FROM collection_versions WHERE collection = %s)',
                            (col,)
                        )
                        cur.execute('DELETE FROM collection_versions WHERE collection = %s', (col,))
                        cur.execute('DELETE FROM user_current_branch WHERE collection = %s', (col,))
            else:
                # 全表还原，使用 DELETE 代替 TRUNCATE CASCADE 避免意外删除关联表数据
                # DELETE 不会级联删除，更加安全可控
                cur.execute(f'DELETE FROM {table_name}')
                # 如果是 dynamic_data，也需要清理相关表
                if table_name == 'dynamic_data':
                    cur.execute('DELETE FROM data_relations')
                    cur.execute('DELETE FROM version_snapshots')
                    cur.execute('DELETE FROM version_relations')
                    cur.execute('DELETE FROM collection_versions')
                    cur.execute('DELETE FROM user_current_branch')

        # INSERT 数据（按 RESTORE_ORDER 顺序）
        for table_name in tables_to_restore:
            if table_name not in BACKUP_TABLE_MAP:
                continue
            _, columns, jsonb_indices, _ = BACKUP_TABLE_MAP[table_name]
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

        # 如果还原了 dynamic_data，需要同步还原相关的 data_relations
        if 'dynamic_data' in tables_to_restore and 'data_relations' not in tables_to_restore:
            if 'data_relations' in table_data:
                records = table_data['data_relations']
                # 如果有 collection 过滤，只还原对应的数据
                if 'dynamic_data' in collection_filters:
                    records = [r for r in records if r.get('collection') in collection_filters['dynamic_data']]
                for record in records:
                    cur.execute(
                        'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
                        'VALUES (%s, %s, %s, %s, %s, %s)',
                        (record.get('collection'), record.get('record_id'), record.get('field_name'),
                         record.get('related_collection'), record.get('related_id'), record.get('branch_id'))
                    )

        conn.commit()
        main_success = True  # 主操作成功
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            # 只有主操作成功时才尝试恢复触发器
            if main_success:
                try:
                    # 恢复触发器（在单独的 try 中执行）
                    cur = conn.cursor()
                    cur.execute('SET session_replication_role = DEFAULT')
                    # 验证恢复是否成功
                    cur.execute('SHOW session_replication_role')
                    result = cur.fetchone()
                    if result and result[0] != 'origin':
                        import logging
                        logging.error(f'Failed to restore session_replication_role: {result[0]}')
                except Exception as e:
                    import logging
                    logging.error(f'Exception restoring session_replication_role: {e}')
                    # 关闭连接而不是返回到池中（连接可能处于异常状态）
                    try:
                        conn.close()
                    except Exception:
                        pass
                    return manifest
                from db import pool
                pool.putconn(conn)
            else:
                # 主操作失败，直接关闭连接
                try:
                    conn.close()
                except Exception:
                    pass

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
