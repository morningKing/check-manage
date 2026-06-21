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
import shutil
import zipfile
import threading
from datetime import datetime, timezone, timedelta
from db import get_db
import psycopg2.extras

# 备份文件存储目录
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backups')

# mem0 向量库目录（与 utils/memory.py 一致；在此重复定义以避免 import memory 的副作用）。
MEM0_STORE_ROOT = os.environ.get(
    'MEM0_STORE_ROOT', os.path.join(os.path.expanduser('~'), '.check-manage', 'mem0'))
_VECTOR_PREFIX = 'vector_store/'


def _add_vector_store_to_zip(zf):
    """把 mem0 向量库目录的全部文件打进 ZIP（vector_store/ 前缀）。返回是否打了内容。"""
    if not os.path.isdir(MEM0_STORE_ROOT):
        return False
    added = False
    for root, _dirs, files in os.walk(MEM0_STORE_ROOT):
        for f in files:
            fp = os.path.join(root, f)
            arc = _VECTOR_PREFIX + os.path.relpath(fp, MEM0_STORE_ROOT).replace('\\', '/')
            zf.write(fp, arc)
            added = True
    return added


def _restore_vector_store(zip_path):
    """从 ZIP 还原向量库目录到 MEM0_STORE_ROOT。向量库没有 upsert 语义，因此是
    全量覆盖（先清空再解出），与备份时刻的 DB 引用 id 配套一致。返回是否还原了内容。"""
    with zipfile.ZipFile(zip_path, 'r') as zf:
        vs = [n for n in zf.namelist() if n.startswith(_VECTOR_PREFIX) and not n.endswith('/')]
        if not vs:
            return False
        if os.path.isdir(MEM0_STORE_ROOT):
            shutil.rmtree(MEM0_STORE_ROOT, ignore_errors=True)
        os.makedirs(MEM0_STORE_ROOT, exist_ok=True)
        for n in vs:
            rel = n[len(_VECTOR_PREFIX):]
            dest = os.path.join(MEM0_STORE_ROOT, rel)
            os.makedirs(os.path.dirname(dest) or MEM0_STORE_ROOT, exist_ok=True)
            with open(dest, 'wb') as out:
                out.write(zf.read(n))
    return True

# 需要备份的业务表及其列定义
# (表名, 列列表, JSONB列索引集合, 中文标签)
BACKUP_TABLES = [
    ('menus', ['id', 'name', 'icon', 'page_id', 'parent_id', '"order"', 'path', 'roles',
               'export_script_id', 'menu_type', 'project_id'], {7}, '菜单配置'),
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

    # 项目级版本管理 (跨 collection 的项目快照)
    ('project_versions', ['id', 'project_menu_id', 'name', 'description', 'version_type', 'parent_version',
                          'status', 'created_by', 'created_at', 'merged_at', 'merged_by', 'is_protected',
                          'records_count', 'initialized_at', 'is_locked', 'locked_at', 'locked_by'], set(), '项目版本'),
    ('project_version_snapshots', ['version_id', 'collection', 'record_id', 'record_data', 'created_at'], {3}, '项目版本快照'),
    ('project_version_relations', ['version_id', 'collection', 'record_id', 'field_name', 'related_collection', 'related_id'], set(), '项目版本关联'),
    ('user_current_project_branch', ['id', 'user_id', 'username', 'project_menu_id', 'branch_id', 'updated_at'], set(), '用户项目分支映射'),

    # 项目依赖
    ('project_dependencies', ['id', 'source_project', 'source_branch', 'target_project', 'target_branch',
                              'relation_type', 'pinned_version', 'is_validated', 'validation_error',
                              'declared_by', 'declared_at', 'updated_at'], set(), '项目依赖'),
    ('project_dependency_relations', ['id', 'dependency_id', 'source_collection', 'source_field',
                                      'target_collection', 'estimated_records', 'validation_status',
                                      'validation_detail', 'validated_at', 'created_at'], set(), '项目依赖关联'),
    ('project_dependency_events', ['id', 'event_type', 'source_project', 'source_branch',
                                   'affected_dependencies', 'severity', 'message', 'created_at',
                                   'resolved_at', 'resolved_by'], set(), '项目依赖事件'),

    # 合并记录
    ('merge_records', ['id', 'source_version_id', 'source_version_name', 'target_branch_id',
                       'target_branch_name', 'project_menu_id', 'strategy', 'merged_by', 'merged_at',
                       'records_created', 'records_updated', 'records_deleted', 'description', 'created_at'], set(), '合并记录'),
    ('merge_backups', ['id', 'merge_id', 'collection', 'backup_type', 'record_id',
                       'old_data', 'new_data', 'old_relations', 'new_relations', 'created_at'], {5, 6, 7, 8}, '合并回滚备份'),

    # Webhook
    ('webhook_rules', ['id', 'name', 'description', 'enabled', 'trigger_event', 'trigger_condition',
                       'webhook_url', 'secret', 'timeout', 'retries', 'execution_order', 'created_at',
                       'updated_at', 'created_by', 'updated_by', 'source_collections',
                       'trigger_timing', 'rollback_on_failure'], {5, 15}, 'Webhook规则'),
    ('webhook_logs', ['id', 'webhook_url', 'event_type', 'request_payload', 'response_status',
                      'response_body', 'error_message', 'duration_ms', 'retry_count', 'success',
                      'created_at', 'rule_id', 'rule_name'], {3}, 'Webhook日志'),
    ('webhook_settings', ['id', 'enabled', 'name', 'webhook_url', 'secret', 'events',
                          'timeout', 'retries', 'updated_at', 'updated_by'], {5}, 'Webhook全局设置'),

    # 触发规则
    ('trigger_rules', ['id', 'name', 'description', 'enabled', 'source_collection', 'trigger_event',
                       'trigger_condition', 'target_collection', 'action_type', 'action_config',
                       'execution_order', 'created_at', 'updated_at'], {6, 9}, '触发规则'),
    ('trigger_logs', ['id', 'rule_id', 'rule_name', 'source_collection', 'source_record_id',
                      'target_collection', 'target_record_id', 'status', 'error_message', 'created_at'], set(), '触发日志'),

    # 通知 / 提醒 / 评论
    ('notifications', ['id', 'user_id', 'type', 'title', 'content', 'source_collection',
                       'source_record_id', 'is_read', 'created_at'], set(), '通知'),
    ('reminders', ['id', 'collection', 'record_id', 'user_id', 'remind_at', 'message',
                   'is_sent', 'created_at'], set(), '提醒'),
    ('record_comments', ['id', 'collection', 'record_id', 'content', 'mentions',
                         'author_id', 'author_name', 'created_at', 'updated_at'], {4}, '记录评论'),

    # UI 配置
    ('column_views', ['id', 'page_id', 'name', 'is_public', 'creator_id', 'is_default',
                      'columns', 'sort_config', 'filter_config', 'group_config',
                      'created_at', 'updated_at'], {6, 7, 8, 9}, '列视图'),
    ('dashboards', ['id', 'name', 'description', 'layout', 'owner_id', 'is_global',
                    'created_at', 'updated_at'], {3}, '仪表盘'),
    ('home_widgets', ['id', 'widget_type', 'title', 'content', 'enabled', '"order"',
                      'visible_roles', 'created_at', 'updated_at'], {3, 6}, '首页组件'),

    # 系统 / AI 全局配置
    ('system_config', ['id', 'system_name', 'system_short_name', 'logo_url',
                       'updated_at', 'updated_by'], set(), '系统配置'),
    ('ai_settings', ['id', 'enabled', 'api_key', 'endpoint', 'model', 'timeout',
                     'max_tokens', 'updated_at'], set(), 'AI设置'),

    # AI Chat (会话 / 消息 / 批任务 / 提示模板)
    ('ai_chat_prompt_templates', ['id', 'user_id', 'name', 'content',
                                  'created_at', 'updated_at'], set(), 'AI提示模板'),
    ('ai_chat_batches', ['id', 'user_id', 'name', 'prompt', 'template_id', 'status',
                         'total', 'done', 'failed', 'created_at', 'completed_at'], set(), 'AI批任务'),
    ('ai_chat_sessions', ['id', 'user_id', 'title', 'opencode_session_id', 'workspace_path',
                          'session_token', 'token_expires_at', 'project_menu_id', 'branch_id',
                          'created_at', 'last_active_at', 'status',
                          'batch_id', 'batch_seq', 'batch_input_file',
                          'error_message', 'last_message_preview'], set(), 'AI会话'),
    ('ai_chat_messages', ['id', 'session_id', 'role', 'content', 'created_at'], {3}, 'AI消息'),
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
    'system_config',
    'ai_settings',
    'dashboards',
    'home_widgets',
    'webhook_settings',
    'webhook_rules',
    'trigger_rules',
    'ai_chat_prompt_templates',
    # Level 2: Self-referencing or simple dependencies
    'collection_versions',
    'project_versions',
    'menus',                # Depends on page_configs
    'etl_logs',             # Depends on etl_tasks
    'column_views',         # Depends on page_configs
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
    # Level 5
    'ai_chat_messages',             # Depends on ai_chat_sessions
]


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers for selective restore (upsert mode + dependency warnings)
# ---------------------------------------------------------------------------

def _get_pk_columns(cur, table_name):
    """Return the PK column names for a table, in PK order.

    Used by upsert-mode restore to build
    `INSERT ... ON CONFLICT (pk_cols) DO UPDATE SET ...`.
    """
    cur.execute("""
        SELECT a.attname
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
        WHERE c.conrelid = %s::regclass AND c.contype = 'p'
        ORDER BY array_position(c.conkey, a.attnum)
    """, (table_name,))
    return [r[0] for r in cur.fetchall()]


def _get_fk_dependents_map(cur):
    """Return {parent_table: [child_table, ...]} extracted from pg_constraint.

    `child_table` rows reference `parent_table` via a foreign key. In a
    replace-mode selective restore, deleting + reinserting a parent_table
    leaves rows in any child_table not also being restored as orphan
    references — that's the case `compute_restore_warnings` flags.
    """
    cur.execute("""
        SELECT DISTINCT
          c.conrelid::regclass::text AS child_table,
          c.confrelid::regclass::text AS parent_table
        FROM pg_constraint c
        WHERE c.contype = 'f'
    """)
    deps = {}
    for child, parent in cur.fetchall():
        # strip schema prefix if any
        child = child.split('.')[-1]
        parent = parent.split('.')[-1]
        deps.setdefault(parent, set()).add(child)
    return {k: sorted(v) for k, v in deps.items()}


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


def create_backup(backup_type='manual', created_by=None, tables=None, include_vector_store=True):
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
        'hasVectorStore': bool(include_vector_store and os.path.isdir(MEM0_STORE_ROOT)),
    }

    # 3. 打包 ZIP
    zip_filename = f'{backup_id}.zip'
    zip_path = os.path.join(BACKUP_DIR, zip_filename)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        for table_name, records in table_data.items():
            zf.writestr(f'{table_name}.json', json.dumps(records, ensure_ascii=False, indent=2))
        if include_vector_store:
            _add_vector_store_to_zip(zf)

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


def restore_backup(zip_path, tables=None, mode='upsert', restore_vector_store=True):
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
            from utils.sequences import reseed_sequences
            # 若按 collection 过滤还原，仅重播种这些 collection；否则全量
            _affected = list(collection_filters['dynamic_data']) if collection_filters.get('dynamic_data') else None
            reseed_sequences(cur, collections=_affected)

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

    # 还原向量库（表还原成功后；目录全量覆盖，与备份时刻的 DB 引用配套）
    if restore_vector_store:
        try:
            _restore_vector_store(zip_path)
        except Exception:
            import logging
            logging.error('restore vector store failed', exc_info=True)
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


# 恢复出厂设置时需要删除的表（按依赖顺序）。
# 分两类:
#   - 业务"数据" (用户输入产生的内容、历史记录、临时状态) → 出厂时清空
#   - 管理员"配置" (webhook 规则、触发规则、UI 自定义、AI 模板、系统设置) → 保留
# 如果某项你觉得分类不对,改下面这张表就好。
FACTORY_RESET_TABLES = [
    # 子表 → 父表的顺序(replica role 已禁外键,但维持顺序便于阅读)
    # AI Chat 数据
    'ai_chat_messages',          # → ai_chat_sessions
    'ai_chat_sessions',          # → users, ai_chat_batches
    'ai_chat_batches',           # → users, ai_chat_prompt_templates
    # 项目级版本管理
    'project_version_relations', # → project_versions
    'project_version_snapshots', # → project_versions
    'user_current_project_branch',
    'project_versions',
    # 项目依赖
    'project_dependency_relations',
    'project_dependency_events',
    'project_dependencies',
    # 合并记录
    'merge_backups',             # → merge_records
    'merge_records',
    # Collection 级版本
    'version_relations',
    'version_snapshots',
    'user_current_branch',
    'collection_versions',
    # 动态数据 + 关联
    'data_relations',
    'dynamic_data',
    # 日志/历史/通知/评论
    'webhook_logs',
    'trigger_logs',
    'operation_logs',
    'etl_logs',
    'notifications',
    'reminders',
    'record_comments',
]

# 系统默认菜单ID（首页、仪表盘、数据工具、系统配置相关）
# 巡检管理(menu-2)及其子项是示例业务数据，恢复出厂时删除
SYSTEM_DEFAULT_MENU_IDS = [
    # 首页
    'menu-1',
    # 仪表盘
    'menu-dashboard',
    # 设置中心（塌缩后的统一管理菜单，取代旧 menu-3* 管理菜单树）
    'menu-settings',
]

# 系统默认页面配置ID - 空列表
# 所有页面配置都是业务相关的，恢复出厂时应全部删除
SYSTEM_DEFAULT_PAGE_CONFIG_IDS = []


def factory_reset(created_by=None):
    """
    恢复出厂设置

    删除所有动态业务数据，保留系统配置。
    执行前自动创建备份（标记为 pre-reset）。

    Parameters
    ----------
    created_by : str
        执行者用户名

    Returns
    -------
    dict
        包含删除统计信息
    """
    # 1. 自动创建备份
    pre_reset_backup = create_backup(
        backup_type='manual',
        created_by=created_by or 'system',
    )
    # 更新备份备注
    note = '恢复出厂设置前自动备份'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE backups SET note = %s WHERE id = %s', (note, pre_reset_backup['id']))

    # 2. 执行删除
    deleted_stats = {}
    conn = None
    main_success = False

    try:
        from db import pool
        conn = pool.getconn()
        cur = conn.cursor()

        # 禁用触发器/FK约束
        cur.execute('SET session_replication_role = replica')

        for table in FACTORY_RESET_TABLES:
            # 检查表是否存在
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = %s
                )
            """, (table,))
            if cur.fetchone()[0]:
                cur.execute(f'DELETE FROM {table}')
                deleted_stats[table] = cur.rowcount
            else:
                deleted_stats[table] = 0

        # 3. 删除动态创建的菜单（保留系统默认）
        if SYSTEM_DEFAULT_MENU_IDS:
            cur.execute(
                f'DELETE FROM menus WHERE id NOT IN ({",".join(["%s"] * len(SYSTEM_DEFAULT_MENU_IDS))})',
                SYSTEM_DEFAULT_MENU_IDS
            )
        else:
            cur.execute('DELETE FROM menus')
        deleted_stats['menus'] = cur.rowcount

        # 4. 删除所有页面配置（业务数据，恢复出厂时应清空）
        if SYSTEM_DEFAULT_PAGE_CONFIG_IDS:
            cur.execute(
                f'DELETE FROM page_configs WHERE id NOT IN ({",".join(["%s"] * len(SYSTEM_DEFAULT_PAGE_CONFIG_IDS))})',
                SYSTEM_DEFAULT_PAGE_CONFIG_IDS
            )
        else:
            cur.execute('DELETE FROM page_configs')
        deleted_stats['page_configs'] = cur.rowcount

        conn.commit()
        main_success = True

    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            if main_success:
                try:
                    cur = conn.cursor()
                    cur.execute('SET session_replication_role = DEFAULT')
                except Exception:
                    conn.close()
                    return {
                        'deletedTables': list(deleted_stats.keys()),
                        'deletedRecords': deleted_stats,
                        'backupId': pre_reset_backup['id'],
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                    }
                pool.putconn(conn)
            else:
                conn.close()

    return {
        'deletedTables': list(deleted_stats.keys()),
        'deletedRecords': deleted_stats,
        'backupId': pre_reset_backup['id'],
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
