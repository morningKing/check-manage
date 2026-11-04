import logging
import os
import json
import shutil
import zipfile
from datetime import datetime, timezone, timedelta
from decimal import Decimal
logger = logging.getLogger(__name__)


# 备份文件存储目录
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'backups')

# 外部文件目录（不在 DB 里；默认随备份一起打包/还原）。在此从 env 直接读，避免
# import utils.memory / config 的副作用。AI 会话工作区（AI_WORKSPACE_ROOT）是临时
# 数据（TTL），故意不纳入备份。
MEM0_STORE_ROOT = os.environ.get(
    'MEM0_STORE_ROOT', os.path.join(os.path.expanduser('~'), '.check-manage', 'mem0'))        # mem0 向量库
DATA_FILES_ROOT = os.environ.get(
    'DATA_FILES_ROOT', os.path.join(os.path.expanduser('~'), '.check-manage', 'data-files'))  # 数据页上传文件
_VECTOR_PREFIX = 'vector_store/'
_DATA_FILES_PREFIX = 'data_files/'


def _add_tree_to_zip(zf, root, prefix):
    """把一个目录的全部文件打进 ZIP（prefix 前缀）。返回是否打了内容。"""
    if not os.path.isdir(root):
        return False
    added = False
    for r, _dirs, files in os.walk(root):
        for f in files:
            fp = os.path.join(r, f)
            zf.write(fp, prefix + os.path.relpath(fp, root).replace('\\', '/'))
            added = True
    return added


def _restore_tree(zip_path, prefix, root):
    """从 ZIP 还原一个目录到 root（全量覆盖：先清空再解出；外部文件无 upsert 语义，
    须与备份时刻的 DB 引用配套）。返回是否还原了内容。"""
    with zipfile.ZipFile(zip_path, 'r') as zf:
        items = [n for n in zf.namelist() if n.startswith(prefix) and not n.endswith('/')]
        if not items:
            return False
        if os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
        for n in items:
            dest = os.path.join(root, n[len(prefix):])
            os.makedirs(os.path.dirname(dest) or root, exist_ok=True)
            with open(dest, 'wb') as out:
                out.write(zf.read(n))
    return True


# 语义明确的薄封装（向后兼容）
def _add_vector_store_to_zip(zf):
    return _add_tree_to_zip(zf, MEM0_STORE_ROOT, _VECTOR_PREFIX)


def _restore_vector_store(zip_path):
    return _restore_tree(zip_path, _VECTOR_PREFIX, MEM0_STORE_ROOT)

# 需要备份的业务表及其列定义
# (表名, 列列表, JSONB列索引集合, 中文标签)
BACKUP_TABLES = [
    ('menus', ['id', 'name', 'icon', 'page_id', 'parent_id', '"order"', 'path', 'roles',
               'export_script_id', 'menu_type', 'project_id'], {7}, '菜单配置'),
    ('page_configs', ['id', 'name', 'description', 'api_endpoint', 'fields', 'created_at', 'updated_at',
                      'export_scripts', 'row_export_scripts', 'api_public', 'validation_script',
                      'api_writable', 'view_config', 'delete_binding', 'row_actions'],
     {4, 7, 8, 12, 13, 14}, '页面配置'),
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
    # meta（既有缺口，本次一并补上）：每条消息的耗时/token/成本元数据，之前
    # 备份/还原会静默丢失。seq（本次修复引入，见 get_batch_results 的
    # ORDER BY m.seq）：显式列名 INSERT 会把每行捕获到的真实 seq 值原样写回
    # （不依赖 _export_table 的导出顺序——它没有 ORDER BY），配合下面
    # restore_backup 里的 setval 重播种，保证还原后"插入即定序"这个不变量不
    # 会被清零重来。旧版备份没有 meta/seq 字段时，restore 阶段的 `present`
    # 过滤逻辑会自动跳过它们，交给数据库默认值（NULL / 序列自增），向后兼容。
    ('ai_chat_messages', ['id', 'session_id', 'role', 'content', 'meta',
                          'created_at', 'seq'], {3, 4}, 'AI消息'),
    # 子代理（subtask/subagent 委托）执行轨迹。id 就是 OpenCode 自己的子会话
    # sessionID，还原时原样写回即可，不需要额外的 id 映射。
    ('ai_chat_subtasks', ['id', 'root_session_id', 'parent_subtask_id', 'parent_part_id',
                          'agent', 'prompt', 'description', 'status', 'error_message',
                          'created_at', 'completed_at'], set(), 'AI子代理'),
    ('ai_chat_subtask_messages', ['id', 'subtask_id', 'role', 'content', 'meta',
                                  'created_at', 'seq'], {3, 4}, 'AI子代理消息'),
    # 会话产出文件的独立记录（新增/修改路径的历史累积）。
    ('ai_chat_session_files', ['id', 'session_id', 'path', 'status',
                               'first_seen_at', 'last_seen_at'], set(), 'AI会话产出文件'),
    # P2 §9.3 Artifact Store 元数据（物理文件在 AI_WORKSPACE_ROOT/artifacts/
    # 下，由备份打包流程随 _snapshot_artifacts 单独导出——见 manifest 的
    # artifacts/ 目录）。
    ('artifacts', ['id', 'owner_user_id', 'name', 'media_type', 'size_bytes',
                   'sha256', 'storage_key', 'status', 'retention_days',
                   'created_at', 'expires_at'], set(), 'AI产物'),
    ('artifact_refs', ['id', 'artifact_id', 'run_id', 'step_id', 'attempt_id',
                       'session_id', 'batch_id', 'relation',
                       'created_at'], set(), 'AI产物引用'),

    # AI Harness（P1-A9）：执行安全/持久化/编排三层的新表。列清单对照
    # init_db.py 引用的 migrations/ DDL（2026_09_17_execution_audit_tables、
    # 2026_09_20_agent_action_gate、2026_09_23_harness_p0/p1/p2）逐列填写，
    # 含后续 ALTER ADD COLUMN 追加列（PG 追加在表尾）。artifacts /
    # artifact_refs 已在上方，不重复登记。还原顺序见下方 RESTORE_ORDER
    # 同步扩充（definitions → runs → steps 等外键依赖序）。
    ('ai_orchestration_definitions',
     ['id', 'version', 'name', 'description', 'enabled', 'owner_user_id',
      'nodes', 'edges', 'retry_policy', 'timeout_policy', 'budget_policy',
      'approval_policy', 'compensation_policy', 'created_at', 'published_at'],
     {6, 7, 8, 9, 10, 11, 12}, 'AI编排定义'),
    ('ai_orchestration_runs',
     ['id', 'definition_id', 'definition_version', 'status', 'current_nodes',
      'run_input_snapshot', 'runtime_manifest_id', 'budget_snapshot',
      'requested_by', 'requested_by_kind', 'error_code', 'error_message',
      'started_at', 'finished_at', 'created_at'],
     {4, 5, 7}, 'AI编排运行'),
    ('ai_orchestration_steps',
     ['id', 'run_id', 'node_id', 'kind', 'name', 'status', 'depends_on',
      'node_def', 'session_id', 'attempt_count', 'output', 'error_code',
      'error_message', 'started_at', 'finished_at', 'updated_at'],
     {6, 7, 10}, 'AI编排步骤'),
    ('ai_runtime_manifests',
     ['id', 'runtime_kind', 'runtime_version', 'model', 'agent_hashes',
      'skill_hashes', 'plugin_hashes', 'mcp_config_hash', 'guidance_hash',
      'workspace_config_hash', 'resource_profile', 'network_policy',
      'created_at'],
     {4, 5, 6, 10, 11}, 'AI运行时清单'),
    # 执行事实层（attempt 含 P1 租约/恢复/预算追加列，budget_snapshot 为 JSONB）
    ('ai_execution_attempts',
     ['id', 'session_id', 'source_type', 'source_id', 'parent_attempt_id',
      'attempt_no', 'operation', 'requested_agent', 'effective_agent',
      'requested_model', 'effective_model', 'agent_resolution',
      'model_resolution', 'raw_prompt_hash', 'effective_prompt_hash',
      'effective_prompt_len', 'prompt_version', 'workspace_path',
      'workspace_config_hash', 'guidance_hash', 'status', 'error_code',
      'error_message', 'started_at', 'finished_at', 'created_at',
      'lease_owner', 'lease_until', 'heartbeat_at', 'fencing_token',
      'queue_started_at', 'dispatch_started_at', 'last_progress_at',
      'recovery_reason', 'checkpoint_id', 'budget_snapshot'],
     {35}, 'AI执行尝试'),
    ('ai_execution_events',
     ['id', 'attempt_id', 'event_seq', 'event_type', 'occurred_at',
      'received_at', 'session_id', 'parent_session_id', 'message_id',
      'part_id', 'tool_call_id', 'parent_part_id', 'subtask_id', 'status',
      'payload', 'redaction_status'],
     {14}, 'AI执行事件'),
    ('ai_execution_checkpoints',
     ['id', 'session_id', 'attempt_id', 'execution_generation',
      'checkpoint_type', 'message_seq', 'opencode_session_id',
      'workspace_manifest_hash', 'completed_effect_ids', 'artifact_refs',
      'context_snapshot', 'is_latest', 'created_at'],
     {8, 9, 10}, 'AI执行检查点'),
    ('ai_execution_effects',
     ['id', 'session_id', 'attempt_id', 'batch_id', 'step_key', 'effect_type',
      'idempotency_key', 'request_hash', 'status', 'external_ref',
      'result_hash', 'created_at', 'committed_at'],
     set(), 'AI副作用账本'),
    ('ai_execution_commands',
     ['id', 'idempotency_key', 'batch_id', 'session_id', 'command_type',
      'requested_by', 'requested_by_kind', 'payload', 'expected_generation',
      'status', 'result_snapshot', 'error_code', 'created_at', 'applied_at'],
     {7, 10}, 'AI执行命令'),
    ('ai_batch_events',
     ['batch_id', 'event_seq', 'event_id', 'event_type', 'aggregate_type',
      'aggregate_id', 'execution_generation', 'payload', 'created_at'],
     {7}, 'AI批次事件'),
    ('ai_delivery_outbox',
     ['id', 'event_id', 'batch_id', 'event_type', 'target_url', 'payload',
      'signature', 'idempotency_key', 'status', 'attempt_count',
      'next_retry_at', 'last_error', 'delivered_at', 'created_at'],
     {5}, 'AI投递发件箱'),
    ('ai_execution_budgets',
     ['id', 'scope_type', 'scope_id', 'max_wall_clock_ms', 'max_tokens',
      'max_cost', 'max_tool_calls', 'max_subagents', 'max_workspace_bytes',
      'max_concurrency', 'on_exceed', 'enabled', 'created_at'],
     set(), 'AI执行预算'),
    ('ai_execution_usage',
     ['session_id', 'batch_id', 'tokens_input', 'tokens_output', 'cost',
      'wall_clock_ms', 'tool_calls', 'subagents', 'workspace_bytes',
      'updated_at'],
     set(), 'AI执行用量'),
    # 动作账本与到位门禁（agent_tool_calls / action_expectations 含
    # 2026_09_20 迁移的 ALTER 追加列：agent / check_type / effect_spec /
    # subagents）
    ('ai_chat_turns',
     ['id', 'session_id', 'batch_id', 'user_id', 'client_request_id',
      'operation', 'status', 'retry_of', 'attempt_id', 'expected_generation',
      'error_code', 'error_message', 'started_at', 'last_event_at',
      'finished_at', 'created_at'],
     set(), 'AI对话轮次'),
    ('agent_tool_calls',
     ['id', 'oc_session_id', 'root_session_id', 'subtask_id', 'message_id',
      'part_id', 'tool', 'args_text', 'state', 'occurred_at', 'agent'],
     set(), 'Agent工具调用账本'),
    ('action_expectations',
     ['id', 'scope_type', 'scope_id', 'name', 'tool', 'args_pattern',
      'require_state', 'min_count', 'source', 'last_status',
      'last_checked_at', 'last_evidence', 'created_at', 'check_type',
      'effect_spec', 'subagents'],
     {14, 15}, '到位期望'),
]

# 表名到定义的映射
BACKUP_TABLE_MAP = {t[0]: t for t in BACKUP_TABLES}

# 备份版本号（用于未来兼容性迁移）
BACKUP_VERSION = 1



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



def _serialize_value(val):
    """将数据库值序列化为 JSON 可存储的格式"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, Decimal):
        # NUMERIC 列（如 ai_execution_usage.cost）psycopg2 返回 Decimal，
        # json.dumps 无 default= 会直接 TypeError——导出前转 float
        return float(val)
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


def _export_table_to_file(cur, table_name, columns, out_path,
                          collection_filter=None, chunk_size=2000):
    """流式导出整表到 JSON 数组文件（服务端游标 fetchmany 分块，内存 O(chunk)）。

    大数据量优化 §1.3（2026-09-28）：原 fetchall 全量载入在 ai_chat_messages /
    operation_logs / agent_tool_calls 达百万行级时进程 OOM。输出为合法 JSON
    数组，与 _export_table 的格式兼容——恢复侧（json.loads）无感。返回行数。
    """
    col_str = ', '.join(columns)
    named = cur.connection.cursor(name=f'_backup_{table_name}')
    try:
        if table_name == 'dynamic_data' and collection_filter:
            named.execute(f'SELECT {col_str} FROM {table_name} WHERE collection = %s',
                          (collection_filter,))
        else:
            named.execute(f'SELECT {col_str} FROM {table_name}')
        clean_cols = [c.strip('"') for c in columns]
        count = 0
        with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('[')
            while True:
                # 显式 list() 化：fetchmany 在测试打桩（MagicMock）下返回
                # truthy 的 Mock 对象，直接判真值会死循环
                rows = list(named.fetchmany(chunk_size) or [])
                if not rows:
                    break
                for row in rows:
                    record = {}
                    for i, col in enumerate(clean_cols):
                        record[col] = _serialize_value(row[i])
                    f.write(('' if count == 0 else ',') +
                            json.dumps(record, ensure_ascii=False))
                    count += 1
            f.write(']')
    finally:
        try:
            named.close()
        except Exception:  # noqa: BLE001
            pass
    return count


def _cleanup_stale_backup_tmp(backup_dir):
    """清理上次备份运行崩溃遗留的 .tmp- 临时导出文件（幂等自愈）。"""
    try:
        for name in os.listdir(backup_dir):
            if name.startswith('.tmp-backup-') and name.endswith('.json'):
                try:
                    os.remove(os.path.join(backup_dir, name))
                except OSError:
                    pass
    except OSError:
        pass



__all__ = [
    'logger',
    'BACKUP_DIR',
    'MEM0_STORE_ROOT',
    'DATA_FILES_ROOT',
    '_VECTOR_PREFIX',
    '_DATA_FILES_PREFIX',
    'Decimal',
    'datetime',
    'timezone',
    'timedelta',
    '_add_tree_to_zip',
    '_restore_tree',
    '_add_vector_store_to_zip',
    '_restore_vector_store',
    'BACKUP_TABLES',
    'BACKUP_TABLE_MAP',
    'BACKUP_VERSION',
    '_ensure_backup_dir',
    '_get_pk_columns',
    '_get_fk_dependents_map',
    '_serialize_value',
    '_export_table',
    'get_backup_table_names',
    '_export_table_to_file',
    '_cleanup_stale_backup_tmp',
]
