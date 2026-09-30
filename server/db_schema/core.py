"""核心业务表（菜单/用户/页面配置/动态数据/Webhook 等）。"""

DDL = """
CREATE TABLE IF NOT EXISTS menus (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    icon        VARCHAR(100),
    page_id     VARCHAR(100),
    parent_id   VARCHAR(100),
    "order"     INTEGER NOT NULL DEFAULT 0,
    path        VARCHAR(500),
    roles       JSONB NOT NULL DEFAULT '["admin","developer","guest"]'::jsonb
);

CREATE TABLE IF NOT EXISTS page_configs (
    id              VARCHAR(100) PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    api_endpoint    VARCHAR(500),
    fields          JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dynamic_data (
    id          VARCHAR(100) NOT NULL,
    collection  VARCHAR(200) NOT NULL,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    version     INTEGER NOT NULL DEFAULT 1,
    branch_id   VARCHAR(100) NOT NULL DEFAULT 'main',
    PRIMARY KEY (id, branch_id)
);

CREATE INDEX IF NOT EXISTS idx_dynamic_data_collection ON dynamic_data(collection);
CREATE INDEX IF NOT EXISTS idx_dynamic_data_coll_branch ON dynamic_data(collection, branch_id);
CREATE INDEX IF NOT EXISTS idx_dynamic_data_coll_branch_created ON dynamic_data(collection, branch_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dynamic_data_gin ON dynamic_data USING gin(data);

-- 关键字搜索加速：预计算列 + pg_trgm GIN 索引（见 utils/search_text.py）。
-- data->>field ILIKE 逐字段扫描在千万级数据下退化为全表扫描；search_text
-- 由写路径（create_item/update_item/batch_create_items + Open API 对应端点）
-- 维护，配合下方的 trigram 索引可以让 ILIKE 走索引。
CREATE EXTENSION IF NOT EXISTS pg_trgm;
ALTER TABLE dynamic_data ADD COLUMN IF NOT EXISTS search_text TEXT;

-- 按字段配置生成的表达式索引：管理员在字段配置里勾选"加速筛选/排序"后，
-- 这里先记一行 pending，utils/field_index_scheduler.py 的后台任务异步
-- CREATE INDEX CONCURRENTLY 建出 (data->>'field') 表达式索引，避免在保存
-- 页面配置的请求里同步等一个可能耗时很久的建索引操作（见 utils/field_indexes.py）。
CREATE TABLE IF NOT EXISTS field_indexes (
    collection    VARCHAR(200) NOT NULL,
    field_name    VARCHAR(200) NOT NULL,
    index_name    VARCHAR(80) NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
    error         TEXT,
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ready_at      TIMESTAMPTZ,
    PRIMARY KEY (collection, field_name)
);
CREATE INDEX IF NOT EXISTS idx_field_indexes_status ON field_indexes(status);

CREATE TABLE IF NOT EXISTS data_relations (
    collection          VARCHAR(200) NOT NULL,
    record_id           VARCHAR(100) NOT NULL,
    field_name          VARCHAR(200) NOT NULL,
    related_collection  VARCHAR(200) NOT NULL,
    related_id          VARCHAR(100) NOT NULL,
    branch_id           VARCHAR(100) NOT NULL DEFAULT 'main',
    PRIMARY KEY (collection, record_id, field_name, related_id, branch_id)
);

CREATE INDEX IF NOT EXISTS idx_data_relations_reverse
    ON data_relations(related_collection, related_id);
CREATE INDEX IF NOT EXISTS idx_data_relations_forward
    ON data_relations(collection, record_id, field_name, branch_id);
CREATE INDEX IF NOT EXISTS idx_data_relations_reverse_branch
    ON data_relations(related_collection, related_id, branch_id);

CREATE TABLE IF NOT EXISTS user_current_branch (
    id              VARCHAR(100) PRIMARY KEY,
    user_id         VARCHAR(100) NOT NULL,
    username        VARCHAR(100) NOT NULL,
    collection      VARCHAR(200) NOT NULL,
    branch_id       VARCHAR(100) NOT NULL DEFAULT 'main',
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, collection)
);

CREATE INDEX IF NOT EXISTS idx_user_current_branch_user ON user_current_branch(user_id);
CREATE INDEX IF NOT EXISTS idx_user_current_branch_collection ON user_current_branch(collection);

CREATE TABLE IF NOT EXISTS users (
    id              VARCHAR(100) PRIMARY KEY,
    username        VARCHAR(100) UNIQUE NOT NULL,
    password_hash   VARCHAR(256) NOT NULL,
    display_name    VARCHAR(200) NOT NULL,
    role            VARCHAR(50)  NOT NULL DEFAULT 'guest',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);

CREATE TABLE IF NOT EXISTS operation_logs (
    id              VARCHAR(100) PRIMARY KEY,
    action          VARCHAR(50) NOT NULL,
    target_type     VARCHAR(100) NOT NULL,
    target_id       VARCHAR(100),
    target_name     VARCHAR(500),
    description     VARCHAR(1000) NOT NULL,
    operator_id     VARCHAR(100) NOT NULL,
    operator_name   VARCHAR(200) NOT NULL,
    operator_role   VARCHAR(50) NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_operation_logs_created_at ON operation_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_operation_logs_action ON operation_logs(action);
CREATE INDEX IF NOT EXISTS idx_operation_logs_target_type ON operation_logs(target_type);
-- Marks a log row as originating from an external API Key call rather than a
-- JWT/UI action by the same operator. operator_id/name/role still attribute
-- the call to the key's bound user (log_api_operation in utils/operation_log.py) —
-- this column is only the "came in via a key" flag, nullable so UI-originated
-- rows stay untouched.
ALTER TABLE operation_logs ADD COLUMN IF NOT EXISTS api_key_id VARCHAR(100);

CREATE TABLE IF NOT EXISTS backups (
    id              VARCHAR(100) PRIMARY KEY,
    name            VARCHAR(500) NOT NULL,
    type            VARCHAR(50) NOT NULL DEFAULT 'manual',
    status          VARCHAR(50) NOT NULL DEFAULT 'completed',
    file_path       VARCHAR(1000),
    file_size       BIGINT DEFAULT 0,
    tables_count    INTEGER DEFAULT 0,
    records_count   INTEGER DEFAULT 0,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    note            TEXT,
    backup_scope    VARCHAR(20) DEFAULT 'full',
    backup_tables   JSONB DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS backup_settings (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    interval        VARCHAR(50) NOT NULL DEFAULT 'daily',
    retention_count INTEGER NOT NULL DEFAULT 10,
    last_backup_at  TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO backup_settings (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS ai_settings (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    api_key         VARCHAR(500) NOT NULL DEFAULT '',
    endpoint        VARCHAR(1000) NOT NULL DEFAULT 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
    model           VARCHAR(200) NOT NULL DEFAULT 'qwen-plus',
    timeout         INTEGER NOT NULL DEFAULT 30,
    max_tokens      INTEGER NOT NULL DEFAULT 1024,
    mem0_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
    embedding_model VARCHAR(200) NOT NULL DEFAULT 'text-embedding-v3',
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO ai_settings (id) VALUES (1) ON CONFLICT DO NOTHING;

-- mem0 长期记忆配置列：新库由上面的 CREATE 直接带上；已有库幂等补列
-- （等价于 add_mem0_settings_columns.py，纳入 init_db 后新部署无需再跑该迁移脚本）。
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS mem0_enabled    BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(200) NOT NULL DEFAULT 'text-embedding-v3';
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS default_chat_model VARCHAR(200) NOT NULL DEFAULT '';
-- 平台内置 MCP（check-manage）启用开关：FALSE 时新建/清空的会话 opencode.json
-- 不再写入内部 MCP 条目（外部 MCP 照常）。默认开启。
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS mcp_internal_enabled BOOLEAN NOT NULL DEFAULT TRUE;
-- 批任务子会话个数上限：每个输入文件对应一个子会话，管理员可在 AI 配置页
-- 调整（utils/batch_repo.get_max_files_per_batch 读取，缺省/非法回落 50）。
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS max_batch_sessions INTEGER NOT NULL DEFAULT 50;

-- External MCP servers registered by an admin and merged into every AI-chat
-- session's opencode.json (alongside the platform's own MCP). `name` is the key
-- used in opencode.json's `mcp` map. `type` is 'remote' (url + headers) or
-- 'local' (command argv + environment).
CREATE TABLE IF NOT EXISTS ai_mcp_servers (
    id          VARCHAR(64) PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    type        VARCHAR(20)  NOT NULL DEFAULT 'remote',
    url         VARCHAR(1000) NOT NULL DEFAULT '',
    command     JSONB NOT NULL DEFAULT '[]'::jsonb,
    headers     JSONB NOT NULL DEFAULT '{}'::jsonb,
    environment JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ==================== system_config 表 ====================
CREATE TABLE IF NOT EXISTS system_config (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    system_name     VARCHAR(200) NOT NULL DEFAULT 'BKB · 数据智能平台',
    system_short_name VARCHAR(50) NOT NULL DEFAULT 'BKB',
    logo_url        VARCHAR(500),
    login_title     VARCHAR(200),
    login_subtitle  VARCHAR(300),
    login_footer    VARCHAR(500),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_by      VARCHAR(100)
);

INSERT INTO system_config (id) VALUES (1) ON CONFLICT DO NOTHING;

-- home_widgets 表：故意不在这里建。见下方"Migration: create home_widgets
-- table if missing" + "Migration: add layout_x/y/w/h columns"——这两段
-- guarded 迁移已经完整覆盖全新建表（带 layout 列 + 播种默认区块）和老库
-- 补列两种场景。之前这里重复放了一份带 layout_y 的 CREATE TABLE IF NOT
-- EXISTS + 无条件 INSERT：老库如果 home_widgets 表已存在但还没有
-- layout_y 列（建表早于 layout 迁移上线），CREATE TABLE IF NOT EXISTS
-- 是空操作，紧跟着的 INSERT 却直接报 "layout_y 字段不存在" 让整个
-- init_db.py 在最开头就崩溃，后面那段本该补上 layout_y 列的迁移根本
-- 没机会跑到。

CREATE TABLE IF NOT EXISTS export_scripts (
    id              VARCHAR(100) PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    language        VARCHAR(50) NOT NULL DEFAULT 'python',
    script          TEXT NOT NULL,
    output_format   VARCHAR(50) NOT NULL DEFAULT 'json',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id              VARCHAR(100) PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    key_hash        VARCHAR(256) NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS validation_scripts (
    id              VARCHAR(100) PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    script          TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etl_tasks (
    id              VARCHAR(100) PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    steps           JSONB NOT NULL DEFAULT '[]'::jsonb,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at     TIMESTAMPTZ,
    last_run_status VARCHAR(50),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etl_logs (
    id              VARCHAR(100) PRIMARY KEY,
    task_id         VARCHAR(100) NOT NULL,
    task_name       VARCHAR(200),
    status          VARCHAR(50) NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    total_records   INTEGER DEFAULT 0,
    success_count   INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    step_results    JSONB DEFAULT '[]'::jsonb,
    error_detail    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_etl_logs_task_id ON etl_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_etl_logs_created_at ON etl_logs(created_at DESC);

-- ETL 异步执行：progress_current/current_step_name 供轮询展示进度，
-- cancel_requested 供取消接口置位、调度器在批次/步骤之间检查。
ALTER TABLE etl_logs ADD COLUMN IF NOT EXISTS progress_current INTEGER DEFAULT 0;
ALTER TABLE etl_logs ADD COLUMN IF NOT EXISTS current_step_name VARCHAR(200);
ALTER TABLE etl_logs ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE;

-- 调度器的认领查询只关心 status='pending' 的行，partial index 让这个查询
-- 不随历史日志增长变慢（同一手法见 idx_dynamic_data_search_text_pending）。
CREATE INDEX IF NOT EXISTS idx_etl_logs_status_pending
  ON etl_logs(started_at) WHERE status = 'pending';

-- ==================== 版本管理表 ====================

CREATE TABLE IF NOT EXISTS collection_versions (
    id              VARCHAR(100) PRIMARY KEY,
    collection      VARCHAR(200) NOT NULL,
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    version_type    VARCHAR(20) NOT NULL DEFAULT 'snapshot',
                    -- 'snapshot' | 'branch'
    parent_version  VARCHAR(100),
                    -- 引用父版本，形成分支树
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
                    -- 'active' | 'merged' | 'archived'
    data_hash       VARCHAR(64),
                    -- SHA256 哈希，快速判断数据是否相同
    records_count   INTEGER DEFAULT 0,
    relations_count INTEGER DEFAULT 0,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    merged_at       TIMESTAMPTZ,
    merged_by       VARCHAR(200),
    merged_into     VARCHAR(100),
    initialized_at  TIMESTAMPTZ,
                    -- 分支数据初始化时间，防止并发初始化
    is_protected    BOOLEAN NOT NULL DEFAULT FALSE,
    FOREIGN KEY (parent_version) REFERENCES collection_versions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cv_collection ON collection_versions(collection);
CREATE INDEX IF NOT EXISTS idx_cv_parent ON collection_versions(parent_version);
CREATE INDEX IF NOT EXISTS idx_cv_status ON collection_versions(status);

CREATE TABLE IF NOT EXISTS version_snapshots (
    version_id      VARCHAR(100) NOT NULL,
    record_id       VARCHAR(100) NOT NULL,
    record_data     JSONB NOT NULL,
    created_at      TIMESTAMPTZ,
    PRIMARY KEY (version_id, record_id),
    FOREIGN KEY (version_id) REFERENCES collection_versions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_vs_version ON version_snapshots(version_id);

CREATE TABLE IF NOT EXISTS version_relations (
    version_id          VARCHAR(100) NOT NULL,
    collection          VARCHAR(200) NOT NULL,
    record_id           VARCHAR(100) NOT NULL,
    field_name          VARCHAR(200) NOT NULL,
    related_collection  VARCHAR(200) NOT NULL,
    related_id          VARCHAR(100) NOT NULL,
    PRIMARY KEY (version_id, collection, record_id, field_name, related_id),
    FOREIGN KEY (version_id) REFERENCES collection_versions(id) ON DELETE CASCADE
);

-- version_collections 表：追踪版本涉及的Collection
CREATE TABLE IF NOT EXISTS version_collections (
    version_id  VARCHAR(100) NOT NULL,
    collection  VARCHAR(200) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (version_id, collection),
    FOREIGN KEY (version_id) REFERENCES collection_versions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_version_collections_version ON version_collections(version_id);
CREATE INDEX IF NOT EXISTS idx_version_collections_collection ON version_collections(collection);

-- ==================== Webhook 配置表 ====================

CREATE TABLE IF NOT EXISTS webhook_settings (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    name            VARCHAR(200) NOT NULL DEFAULT '合并通知',
    webhook_url     VARCHAR(1000) NOT NULL DEFAULT '',
    secret          VARCHAR(200) NOT NULL DEFAULT '',
    events          JSONB NOT NULL DEFAULT '["merge"]'::jsonb,
    timeout         INTEGER NOT NULL DEFAULT 30,
    retries         INTEGER NOT NULL DEFAULT 3,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_by      VARCHAR(200)
);

INSERT INTO webhook_settings (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS webhook_logs (
    id              VARCHAR(100) PRIMARY KEY,
    webhook_url     VARCHAR(1000) NOT NULL,
    event_type      VARCHAR(50) NOT NULL,
    request_payload JSONB NOT NULL,
    response_status INTEGER,
    response_body   TEXT,
    error_message   TEXT,
    duration_ms     INTEGER,
    retry_count     INTEGER DEFAULT 0,
    success         BOOLEAN NOT NULL DEFAULT FALSE,
    rule_id         VARCHAR(100),
    rule_name       VARCHAR(200),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wl_event_type ON webhook_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_wl_created_at ON webhook_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wl_success ON webhook_logs(success);

--- ==================== Webhook 规则表 ====================

CREATE TABLE IF NOT EXISTS webhook_rules (
    id              VARCHAR(100) PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    source_collections JSONB DEFAULT '[]'::jsonb, -- 数组，多个数据页；空数组表示全局（如 merge）
    trigger_event   VARCHAR(50) NOT NULL, -- create/update/delete/merge
    trigger_condition JSONB DEFAULT '{}'::jsonb,  -- 可选条件
    webhook_url     VARCHAR(1000) NOT NULL,
    secret          VARCHAR(200) DEFAULT '',
    timeout         INTEGER DEFAULT 30,
    retries         INTEGER DEFAULT 3,
    execution_order INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    created_by      VARCHAR(200),
    updated_by      VARCHAR(200)
);

CREATE INDEX IF NOT EXISTS idx_wr_event ON webhook_rules(trigger_event);
CREATE INDEX IF NOT EXISTS idx_wr_enabled ON webhook_rules(enabled);

-- ==================== AI Chat 表 ====================
CREATE TABLE IF NOT EXISTS ai_chat_sessions (
    id                  VARCHAR(100) PRIMARY KEY,
    user_id             VARCHAR(100) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               VARCHAR(500),
    opencode_session_id VARCHAR(200),
    workspace_path      TEXT NOT NULL,
    session_token       VARCHAR(64) NOT NULL UNIQUE,
    token_expires_at    TIMESTAMPTZ NOT NULL,
    project_menu_id     VARCHAR(100),
    branch_id           VARCHAR(100) DEFAULT 'main',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_active_at      TIMESTAMPTZ DEFAULT NOW(),
    status              VARCHAR(20) DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_chat_sess_user
    ON ai_chat_sessions(user_id, last_active_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_sess_token
    ON ai_chat_sessions(session_token);

CREATE TABLE IF NOT EXISTS ai_chat_messages (
    id          VARCHAR(100) PRIMARY KEY,
    session_id  VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    role        VARCHAR(20) NOT NULL,
    content     JSONB NOT NULL,
    meta        JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_msg_sess
    ON ai_chat_messages(session_id, created_at);
-- Per-assistant-message execution metadata (duration / tokens / cost). Idempotent
-- add for existing deployments.
ALTER TABLE ai_chat_messages ADD COLUMN IF NOT EXISTS meta JSONB;

-- ==================== 子代理（subtask/subagent 委托）执行轨迹 ====================
-- id 就是 OpenCode 自己的子会话 sessionID（ses... 格式），不发明并行的合成 id——
-- 子代理总是先在 OpenCode 侧存在、我们才从 SubtaskPart 里发现它，跟顶层会话
-- "先建行再补 opencode_session_id" 的顺序正好相反。
CREATE TABLE IF NOT EXISTS ai_chat_subtasks (
    id                VARCHAR(100) PRIMARY KEY,
    root_session_id   VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    parent_subtask_id VARCHAR(100) REFERENCES ai_chat_subtasks(id) ON DELETE CASCADE,
    parent_part_id    VARCHAR(100),
    agent             TEXT,
    prompt            TEXT,
    description       TEXT,
    status            VARCHAR(20) NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running','completed','failed')),
    error_message     TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    completed_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_chat_subtask_root ON ai_chat_subtasks(root_session_id);
CREATE INDEX IF NOT EXISTS idx_chat_subtask_parent ON ai_chat_subtasks(parent_subtask_id);

CREATE TABLE IF NOT EXISTS ai_chat_subtask_messages (
    id          VARCHAR(100) PRIMARY KEY,
    subtask_id  VARCHAR(100) NOT NULL REFERENCES ai_chat_subtasks(id) ON DELETE CASCADE,
    role        VARCHAR(20) NOT NULL,
    content     JSONB NOT NULL,
    meta        JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    seq         BIGSERIAL
);
CREATE INDEX IF NOT EXISTS idx_chat_subtask_msg_subtask ON ai_chat_subtask_messages(subtask_id, seq);

-- ==================== 会话产出文件记录 ====================
-- 自动记录每个会话工作区里出现过的新增/修改文件路径（git 扫描结果幂等
-- upsert），除会话本身的「变更文件」面板外提供独立、可查询的记录。
CREATE TABLE IF NOT EXISTS ai_chat_session_files (
    id            BIGSERIAL PRIMARY KEY,
    session_id    VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    path          TEXT NOT NULL,
    status        VARCHAR(20) NOT NULL CHECK (status IN ('added','modified')),
    data_file_id  VARCHAR(100),
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (session_id, path)
);
CREATE INDEX IF NOT EXISTS idx_chat_session_files_sess ON ai_chat_session_files(session_id);
-- 老库补齐列（幂等）。不设外键指向 data_files：备份还原时两张表顺序独立，
-- 松引用即可（值始终是 data_files.id 或 NULL）。
ALTER TABLE ai_chat_session_files ADD COLUMN IF NOT EXISTS data_file_id VARCHAR(100);

-- ==================== 智能客服：实例表 + 会话增列 ====================
CREATE TABLE IF NOT EXISTS kefu_instances (
  id               VARCHAR(100) PRIMARY KEY,
  slug             VARCHAR(100) NOT NULL UNIQUE,
  name             VARCHAR(200) NOT NULL,
  agent            TEXT,
  model            TEXT,
  system_prompt    TEXT,
  welcome_message  TEXT,
  guided_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
  branding         JSONB NOT NULL DEFAULT '{}'::jsonb,
  bot_user_id      VARCHAR(100) NOT NULL REFERENCES users(id),
  enabled          BOOLEAN NOT NULL DEFAULT true,
  rate_limit       JSONB NOT NULL DEFAULT '{}'::jsonb,
  panel_blocks     JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS kefu_instance_id VARCHAR(100) REFERENCES kefu_instances(id) ON DELETE SET NULL;
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS visitor_id     VARCHAR(100);
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS needs_human    BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS human_takeover BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS human_agent_id VARCHAR(100);
CREATE INDEX IF NOT EXISTS idx_chat_sess_kefu ON ai_chat_sessions(kefu_instance_id, visitor_id);

CREATE TABLE IF NOT EXISTS kefu_faq_items (
  id           VARCHAR(100) PRIMARY KEY,
  instance_id  VARCHAR(100) NOT NULL REFERENCES kefu_instances(id) ON DELETE CASCADE,
  question     TEXT NOT NULL,
  answer       TEXT NOT NULL,
  category     VARCHAR(100),
  sort_order   INTEGER NOT NULL DEFAULT 0,
  click_count  INTEGER NOT NULL DEFAULT 0,
  enabled      BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kefu_faq_instance ON kefu_faq_items(instance_id, sort_order);

-- ==================== autoSequence 原子计数器表 ====================
CREATE TABLE IF NOT EXISTS dynamic_sequences (
    collection    VARCHAR(200) NOT NULL,
    branch_id     VARCHAR(100) NOT NULL DEFAULT 'main',
    field_name    VARCHAR(200) NOT NULL,
    current_value BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (collection, branch_id, field_name)
);

-- ==================== 工作流引擎表 ====================
CREATE TABLE IF NOT EXISTS workflow_definitions (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    stages      JSONB NOT NULL DEFAULT '[]'::jsonb,
    edges       JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
-- 迁移：为已存在的库补 edges 列（图形化 DAG + 条件边）
ALTER TABLE workflow_definitions ADD COLUMN IF NOT EXISTS edges JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE TABLE IF NOT EXISTS workflow_instances (
    id               VARCHAR(100) PRIMARY KEY,
    workflow_id      VARCHAR(100) NOT NULL,
    status           VARCHAR(20) NOT NULL DEFAULT 'running',
    current_stage_id VARCHAR(100),
    active_stages    JSONB NOT NULL DEFAULT '[]'::jsonb,
    chain            JSONB NOT NULL DEFAULT '[]'::jsonb,
    history          JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    started_by       VARCHAR(100),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
-- 迁移：并行多活动分支（v2）。补列并回填运行中实例的当前活动分支。
ALTER TABLE workflow_instances ADD COLUMN IF NOT EXISTS active_stages JSONB NOT NULL DEFAULT '[]'::jsonb;
UPDATE workflow_instances wi SET active_stages = COALESCE((
    SELECT jsonb_agg(jsonb_build_object('stageId', e->>'stageId', 'collection', e->>'collection', 'recordId', e->>'recordId'))
    FROM jsonb_array_elements(wi.chain) e WHERE e->>'stageId' = wi.current_stage_id
), '[]'::jsonb)
WHERE wi.status = 'running' AND (wi.active_stages IS NULL OR wi.active_stages = '[]'::jsonb);
CREATE INDEX IF NOT EXISTS idx_wf_inst_status ON workflow_instances(status);
CREATE INDEX IF NOT EXISTS idx_wf_inst_current ON workflow_instances(current_stage_id);
CREATE INDEX IF NOT EXISTS idx_wf_inst_workflow ON workflow_instances(workflow_id);

-- ==================== 导入历史表 ====================

CREATE TABLE IF NOT EXISTS import_runs (
    id              VARCHAR(100) PRIMARY KEY,
    page_id         VARCHAR(100) NOT NULL,
    collection      VARCHAR(200) NOT NULL,
    branch_id       VARCHAR(100) NOT NULL DEFAULT 'main',
    file_name       VARCHAR(500) NOT NULL,
    success_count   INTEGER NOT NULL DEFAULT 0,
    created_count   INTEGER NOT NULL DEFAULT 0,
    updated_count   INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'success',
    operator        VARCHAR(100),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_import_runs_page_created ON import_runs(page_id, created_at DESC);

CREATE TABLE IF NOT EXISTS import_run_failures (
    id              VARCHAR(100) PRIMARY KEY,
    run_id          VARCHAR(100) NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    record_id       VARCHAR(200) NOT NULL,
    original_record JSONB NOT NULL,
    payload         JSONB NOT NULL,
    reason          TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_import_run_failures_run_id ON import_run_failures(run_id);
"""
