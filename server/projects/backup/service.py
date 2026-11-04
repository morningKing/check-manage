"""
备份与还原核心逻辑 + 定时备份调度器

职责：
- 导出全部业务表数据为 JSON，打包 ZIP
- 从 ZIP 还原数据（单事务，失败回滚）
- 后台线程轮询定时备份设置
"""

import logging
import os
import json
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from db import get_db
import psycopg2.extras

from .io import (BACKUP_DIR, BACKUP_TABLES, BACKUP_TABLE_MAP,
                 BACKUP_VERSION, DATA_FILES_ROOT, MEM0_STORE_ROOT,
                 _DATA_FILES_PREFIX, _add_tree_to_zip,
                 _add_vector_store_to_zip, _cleanup_stale_backup_tmp,
                 _ensure_backup_dir, _export_table,
                 _export_table_to_file, _get_fk_dependents_map,
                 _get_pk_columns, _restore_tree, _restore_vector_store)
logger = logging.getLogger(__name__)


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
    'system_config',
    'ai_settings',
    'dashboards',
    'home_widgets',
    'webhook_settings',
    'webhook_rules',
    'trigger_rules',
    'ai_chat_prompt_templates',
    # AI Harness 无依赖表（P1-A9）
    'ai_orchestration_definitions', # AI编排定义（无外键依赖）
    'ai_runtime_manifests',         # AI运行时清单（无外键依赖）
    'ai_execution_commands',        # AI执行命令（无外键依赖）
    'ai_batch_events',              # AI批次事件（无外键依赖）
    'ai_delivery_outbox',           # AI投递发件箱（无外键依赖）
    'ai_execution_budgets',         # AI执行预算（无外键依赖）
    'action_expectations',          # 到位期望（无外键依赖）
    # Level 2: Self-referencing or simple dependencies
    'collection_versions',
    'project_versions',
    'menus',                # Depends on page_configs
    'etl_logs',             # Depends on etl_tasks
    'column_views',         # Depends on page_configs
    # AI Harness 编排链（P1-A9）：ai_chat_sessions.orchestration_run_id
    # 外键引用 runs，故 runs/steps 必须排在 Level 4 的 ai_chat_sessions 之前
    'ai_orchestration_runs',        # Depends on ai_orchestration_definitions
    'ai_orchestration_steps',       # Depends on ai_orchestration_runs
    # Level 3: Multiple dependencies on Level 1/2
    'data_relations',
    'operation_logs',
    'user_current_branch',
    'user_current_project_branch',
    'version_snapshots',
    'version_relations',
    'project_version_snapshots',
    'project_version_relations',
    'project_dependencies',         # Depends on project_versions/menus
    'merge_records',
    'record_comments',
    'reminders',
    'notifications',
    'ai_chat_batches',              # Depends on users, ai_chat_prompt_templates
    # Level 4: Deeper dependencies
    'project_dependency_relations', # Depends on project_dependencies
    'project_dependency_events',
    'merge_backups',                # Depends on merge_records
    'webhook_logs',                 # Depends on webhook_rules
    'trigger_logs',                 # Depends on trigger_rules
    'ai_chat_sessions',             # Depends on users, ai_chat_batches
    'ai_chat_subtasks',             # Depends on ai_chat_sessions
    # Level 5
    'ai_chat_messages',             # Depends on ai_chat_sessions
    'ai_chat_subtask_messages',     # Depends on ai_chat_subtasks
    'ai_chat_session_files',        # Depends on ai_chat_sessions
    # AI Harness 执行事实层（P1-A9）：依赖 ai_chat_sessions / attempts
    'ai_execution_attempts',        # Depends on ai_chat_sessions（parent_attempt_id 自引用）
    'ai_chat_turns',                # Depends on ai_chat_sessions / ai_chat_batches
    'ai_execution_events',          # Depends on ai_execution_attempts
    'ai_execution_checkpoints',     # Depends on ai_chat_sessions / attempts
    'ai_execution_effects',         # Depends on ai_chat_sessions
    'ai_execution_usage',           # Depends on ai_chat_sessions
    'agent_tool_calls',             # Depends on ai_chat_subtasks
    'artifacts',
    'artifact_refs',
]



def compute_restore_warnings(tables_to_restore, mode):
    """Identify tables that would be left with orphan FK references after a
    selective replace-mode restore. Returns [{table, dependentsAtRisk}, ...].

    * `mode == 'upsert'` → always empty (upsert never DELETEs, no orphans).
    * `tables_to_restore` empty → empty (full restore, all tables wiped
      together, no selective orphan risk inside the backup scope).
    * Otherwise, for each table in the set, list FK-dependent tables that
      are NOT in the set.
    """
    if mode != 'replace' or not tables_to_restore:
        return []
    restore_set = set(tables_to_restore)
    with get_db() as conn:
        cur = conn.cursor()
        deps = _get_fk_dependents_map(cur)
    warnings = []
    for t in sorted(restore_set):
        dependents_at_risk = [d for d in deps.get(t, []) if d not in restore_set]
        if dependents_at_risk:
            warnings.append({
                'table': t,
                'dependentsAtRisk': dependents_at_risk,
            })
    return warnings



def create_backup(backup_type='manual', created_by=None, tables=None,
                  include_vector_store=True, include_data_files=True):
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
    _cleanup_stale_backup_tmp(BACKUP_DIR)

    # 1. 导出指定表数据
    with get_db() as conn:
        cur = conn.cursor()
        table_data = {}
        table_files = {}  # table_name -> 流式临时文件路径（无 collection 过滤的整表）
        for table_name in tables_to_backup:
            _, columns, _, _ = BACKUP_TABLE_MAP[table_name]

            # 检查是否有 collection 过滤
            if table_name in collection_filters:
                # 按 collection 分别导出（collection 显式指定，规模有界，保持内存）
                records = []
                for col in collection_filters[table_name]:
                    col_records = _export_table(cur, table_name, columns, collection_filter=col)
                    records.extend(col_records)
                table_data[table_name] = records
                table_stats[table_name] = len(records)
            else:
                # 整表流式导出到临时文件（§1.3：不再 fetchall 全量载入内存）
                tmp_path = os.path.join(
                    BACKUP_DIR, f'.tmp-backup-{backup_id}-{table_name}.json')
                table_stats[table_name] = _export_table_to_file(
                    cur, table_name, columns, tmp_path)
                table_files[table_name] = tmp_path
            total_records += table_stats[table_name]

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
        'hasVectorStore': bool(include_vector_store and os.path.isdir(MEM0_STORE_ROOT)),
        'hasDataFiles': bool(include_data_files and os.path.isdir(DATA_FILES_ROOT)),
    }

    # 3. 打包 ZIP
    zip_filename = f'{backup_id}.zip'
    zip_path = os.path.join(BACKUP_DIR, zip_filename)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        # 按 tables_to_backup 顺序写入（流式临时文件与内存 records 两种来源）
        for table_name in tables_to_backup:
            if table_name in table_files:
                tmp_path = table_files[table_name]
                zf.write(tmp_path, arcname=f'{table_name}.json')
                try:
                    os.remove(tmp_path)
                    table_files[table_name] = None
                except OSError:
                    pass
            elif table_name in table_data:
                zf.writestr(f'{table_name}.json',
                            json.dumps(table_data[table_name], ensure_ascii=False, indent=2))
        if include_vector_store:
            _add_vector_store_to_zip(zf)
        if include_data_files:
            _add_tree_to_zip(zf, DATA_FILES_ROOT, _DATA_FILES_PREFIX)
        # P2 §9.3：Artifact Store 物理文件随备份导出（元数据表已入
        # BACKUP_TABLES）——workspace 可回收但产物不丢
        try:
            from config import AI_WORKSPACE_ROOT as _awr
            _artifacts_root = os.path.join(_awr, 'artifacts')
            if os.path.isdir(_artifacts_root):
                if _add_tree_to_zip(zf, _artifacts_root, 'artifacts/'):
                    manifest['hasArtifacts'] = True
        except Exception:
            pass  # 产物导出失败不阻断备份主体（元数据表已在包内）

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


def restore_backup(zip_path, tables=None, mode='upsert',
                   restore_vector_store=True, restore_data_files=True):
    """
    从 ZIP 备份文件还原数据(单事务,失败回滚)。

    Parameters
    ----------
    zip_path : str
        ZIP 备份文件路径
    tables : list[str] | None
        None = 还原备份中的所有表(始终走 replace 语义,因为所有表一起被替换,不会产生跨表孤儿)
        ['table1'] = 只还原指定表(必须在备份中)
        ['dynamic_data:collection1'] = 只还原指定 collection 的数据
    mode : str
        仅在 `tables` 非空时生效:
        * 'upsert' (默认) — 不 DELETE,逐行 INSERT ... ON CONFLICT DO UPDATE。
          备份里的行会覆盖现存行,备份没有但 DB 里有的行保留。这意味着
          其他表的外键不会因为我们 DELETE 了被引用方而变孤儿。
        * 'replace' — 沿用旧行为:DELETE FROM <table> (或 collection 范围)
          + 普通 INSERT。会让备份当时不存在的行从 DB 消失;路由层应已经
          根据 `compute_restore_warnings` 让用户确认过孤儿风险。

    Returns
    -------
    dict
        manifest 信息(仅包含 backup 自带元数据;路由层负责把 warnings 拼上)
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

        # tables=None 是全量还原 → 永远走 replace 语义(所有表一起被替换,
        # 跨表孤儿不会跨出备份本身的范围)。tables 非空时才看 mode。
        effective_mode = 'replace' if not tables else mode

        # ----- DELETE 阶段(仅 replace 模式) -----
        if effective_mode == 'replace':
            for table_name in tables_to_restore:
                if table_name in collection_filters:
                    for col in collection_filters[table_name]:
                        cur.execute(f'DELETE FROM {table_name} WHERE collection = %s', (col,))
                        if table_name == 'dynamic_data':
                            # 正向 + 反向 data_relations 都要删,否则反向那一行变孤儿
                            cur.execute(
                                'DELETE FROM data_relations '
                                'WHERE collection = %s OR related_collection = %s',
                                (col, col),
                            )
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
                    cur.execute(f'DELETE FROM {table_name}')
                    if table_name == 'dynamic_data':
                        cur.execute('DELETE FROM data_relations')
                        cur.execute('DELETE FROM version_snapshots')
                        cur.execute('DELETE FROM version_relations')
                        cur.execute('DELETE FROM collection_versions')
                        cur.execute('DELETE FROM user_current_branch')

        # ----- INSERT 阶段 -----
        # replace 模式:已经 DELETE 干净了,普通 INSERT。
        # upsert 模式:没 DELETE,需要 ON CONFLICT (pk) DO UPDATE,跳过完全
        # 相同的现存行/覆盖被改的现存行。PK 列从 pg_constraint 实时查。
        pk_cache = {}  # {table_name: [pk_col1, pk_col2, ...]}
        for table_name in tables_to_restore:
            if table_name not in BACKUP_TABLE_MAP:
                continue
            _, columns, jsonb_indices, _ = BACKUP_TABLE_MAP[table_name]
            records = table_data.get(table_name, [])
            if not records:
                continue
            clean_cols = [c.strip('"') for c in columns]
            # JSONB 列按列名识别（下面只用备份里实际存在的列，位置索引会变）
            jsonb_names = {clean_cols[i] for i in jsonb_indices}
            # 只使用备份记录中实际存在的列，兼容旧备份：缺失的列（如旧版菜单备份
            # 没有 menu_type/project_id）由数据库列默认值填充，而不是写入显式 NULL。
            present = [
                (col_q, col_c)
                for col_q, col_c in zip(columns, clean_cols)
                if any(col_c in r for r in records)
            ]
            use_cols_q = [c for c, _ in present]
            use_cols_c = [c for _, c in present]
            col_str = ', '.join(use_cols_q)
            placeholders = ', '.join(['%s'] * len(use_cols_c))

            if effective_mode == 'upsert':
                if table_name not in pk_cache:
                    pk_cache[table_name] = _get_pk_columns(cur, table_name)
                pk_cols = pk_cache[table_name]
                if pk_cols:
                    non_pk = [c for c in use_cols_c if c not in pk_cols]
                    set_clause = ', '.join(f'{c} = EXCLUDED.{c}' for c in non_pk) \
                        if non_pk else None
                    pk_target = ', '.join(pk_cols)
                    if set_clause:
                        sql = (f'INSERT INTO {table_name} ({col_str}) VALUES ({placeholders}) '
                               f'ON CONFLICT ({pk_target}) DO UPDATE SET {set_clause}')
                    else:
                        # 全列都是 PK(纯联结表),冲突时啥也不改
                        sql = (f'INSERT INTO {table_name} ({col_str}) VALUES ({placeholders}) '
                               f'ON CONFLICT ({pk_target}) DO NOTHING')
                else:
                    # 极个别表没 PK(理论上不会发生于业务表);退回 INSERT,
                    # 重复行会写两遍 — 这种表很少,可接受
                    sql = f'INSERT INTO {table_name} ({col_str}) VALUES ({placeholders})'
            else:
                sql = f'INSERT INTO {table_name} ({col_str}) VALUES ({placeholders})'

            for record in records:
                values = []
                for col in use_cols_c:
                    val = record.get(col)
                    if col in jsonb_names and val is not None:
                        val = psycopg2.extras.Json(val)
                    values.append(val)
                cur.execute(sql, values)

        # 如果还原了 dynamic_data，需要同步还原相关的 data_relations。
        # data_relations 没有自然的"id"列,主键是复合的;
        # replace 模式下上面已经清掉了,这里直接 INSERT;
        # upsert 模式下用 ON CONFLICT DO NOTHING(主键复合避免重复行)。
        if 'dynamic_data' in tables_to_restore and 'data_relations' not in tables_to_restore:
            if 'data_relations' in table_data:
                records = table_data['data_relations']
                if 'dynamic_data' in collection_filters:
                    records = [r for r in records if r.get('collection') in collection_filters['dynamic_data']]
                if effective_mode == 'upsert':
                    rel_sql = (
                        'INSERT INTO data_relations (collection, record_id, field_name, '
                        '  related_collection, related_id, branch_id) '
                        'VALUES (%s, %s, %s, %s, %s, %s) '
                        'ON CONFLICT DO NOTHING'
                    )
                else:
                    rel_sql = (
                        'INSERT INTO data_relations (collection, record_id, field_name, '
                        '  related_collection, related_id, branch_id) '
                        'VALUES (%s, %s, %s, %s, %s, %s)'
                    )
                for record in records:
                    cur.execute(rel_sql, (
                        record.get('collection'), record.get('record_id'), record.get('field_name'),
                        record.get('related_collection'), record.get('related_id'), record.get('branch_id'),
                    ))

        # 还原 dynamic_data 后重播种序列计数器，避免后续创建与还原记录重号
        _restored_dynamic = (tables is None) or any(
            (t == 'dynamic_data' or t.startswith('dynamic_data:')) for t in tables
        )
        if _restored_dynamic:
            from data.sequences import reseed_sequences
            # 若按 collection 过滤还原，仅重播种这些 collection；否则全量
            _affected = list(collection_filters['dynamic_data']) if collection_filters.get('dynamic_data') else None
            reseed_sequences(cur, collections=_affected)

        # 还原 ai_chat_messages 后重播种 seq 列背后的真实 Postgres 序列。
        # 上面 INSERT 阶段把每行备份里捕获到的原始 seq 值原样写回（不依赖
        # _export_table 的导出顺序——每行携带自己的真实值，_export_table
        # 对这张表没有 ORDER BY，导出顺序本就不可信），但序列对象自身的
        # nextval() 计数器不会跟着这些手写值自动前移，还停在还原之前的位置。
        # 不重播种的话，还原后第一条新插入的消息会拿到一个 <= 已还原数据里
        # 最大 seq 的值，与还原数据重新打平——get_batch_results 靠 seq 定序
        # 的前提就被还原操作本身破坏了。这是 backup.py 里第一次出现要重播种
        # 真正的 Postgres 序列（而不是 dynamic_sequences 表那种应用层计数器），
        # 之前从没处理过，此处新增。空表兜底为 1（COALESCE），避免 setval
        # 在 MAX(seq) 为 NULL 时报错。
        if 'ai_chat_messages' in tables_to_restore:
            cur.execute(
                "SELECT setval(pg_get_serial_sequence('ai_chat_messages', 'seq'), "
                "  COALESCE((SELECT MAX(seq) FROM ai_chat_messages), 1))"
            )

        # 同样的道理：ai_chat_subtask_messages.seq 也是真正的 Postgres 序列，
        # 还原后必须重播种，否则新插入的子代理消息会拿到 <= 已还原数据里最大
        # seq 的值，subtask_repo 靠 seq 定序的前提就被还原操作本身破坏了。
        if 'ai_chat_subtask_messages' in tables_to_restore:
            cur.execute(
                "SELECT setval(pg_get_serial_sequence('ai_chat_subtask_messages', 'seq'), "
                "  COALESCE((SELECT MAX(seq) FROM ai_chat_subtask_messages), 1))"
            )

        # ai_chat_session_files.id 同样是 BIGSERIAL——还原时原样写回备份里的
        # id，序列计数器需要跟着重播种，否则新插入的记录会与还原数据撞 id。
        if 'ai_chat_session_files' in tables_to_restore:
            cur.execute(
                "SELECT setval(pg_get_serial_sequence('ai_chat_session_files', 'id'), "
                "  COALESCE((SELECT MAX(id) FROM ai_chat_session_files), 1))"
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

    # 还原外部文件目录（表还原成功后；全量覆盖，与备份时刻的 DB 引用配套）
    if restore_vector_store:
        try:
            _restore_vector_store(zip_path)
        except Exception:
            import logging
            logging.error('restore vector store failed', exc_info=True)
    if restore_data_files:
        try:
            _restore_tree(zip_path, _DATA_FILES_PREFIX, DATA_FILES_ROOT)
        except Exception:
            import logging
            logging.error('restore data files failed', exc_info=True)
    return manifest


def delete_backup_file(file_path):
    """删除磁盘上的备份文件"""
    try:
        if file_path and os.path.isfile(file_path):
            os.remove(file_path)
    except OSError:
        pass



__all__ = [
    'logger',
    'datetime',
    'timezone',
    'timedelta',
    'get_db',
    'RESTORE_ORDER',
    'compute_restore_warnings',
    'create_backup',
    'restore_backup',
    'delete_backup_file',
]
