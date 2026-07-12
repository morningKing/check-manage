import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import psycopg2
import psycopg2.extras
from config import DB_CONFIG
from seed_data import MENUS, PAGE_CONFIGS, DYNAMIC_DATA
from utils.search_text import compute_search_text
import json

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
    system_name     VARCHAR(200) NOT NULL DEFAULT '巡检用例管理系统',
    system_short_name VARCHAR(50) NOT NULL DEFAULT '巡检管理',
    logo_url        VARCHAR(500),
    login_title     VARCHAR(200),
    login_subtitle  VARCHAR(300),
    login_footer    VARCHAR(500),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_by      VARCHAR(100)
);

INSERT INTO system_config (id) VALUES (1) ON CONFLICT DO NOTHING;

-- ==================== home_widgets 表 ====================
CREATE TABLE IF NOT EXISTS home_widgets (
    id              VARCHAR(100) PRIMARY KEY,
    widget_type     VARCHAR(50) NOT NULL,
    title           VARCHAR(200),
    content         JSONB,
    enabled         BOOLEAN DEFAULT TRUE,
    "order"         INTEGER DEFAULT 0,
    visible_roles   JSONB DEFAULT '["admin","developer","guest"]',
    layout_x        INTEGER NOT NULL DEFAULT 0,
    layout_y        INTEGER NOT NULL DEFAULT 0,
    layout_w        INTEGER NOT NULL DEFAULT 12,
    layout_h        INTEGER NOT NULL DEFAULT 4,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 默认首页区块数据
INSERT INTO home_widgets (id, widget_type, title, content, enabled, "order", layout_y) VALUES
('welcome', 'welcome', '欢迎',
 '{"heading": "欢迎使用巡检用例管理系统", "description": "本系统支持动态配置菜单和页面，实现灵活的数据管理。"}',
 true, 1, 0),
('stats', 'stats', '系统概览',
 '{"items": [{"type": "menuCount", "label": "菜单数量", "icon": "Document"}, {"type": "pageCount", "label": "页面配置", "icon": "Files"}, {"type": "fieldCount", "label": "字段配置", "icon": "Setting"}]}',
 true, 2, 4),
('quick-links', 'quick-links', '快捷入口',
 '{"links": [{"name": "菜单管理", "path": "/admin/menu", "icon": "Menu"}, {"name": "页面配置", "path": "/admin/page-config", "icon": "Files"}, {"name": "批量导出", "path": "", "icon": "Download", "action": "batchExport"}]}',
 true, 3, 8),
('system-info', 'system-info', '系统说明',
 '{"markdown": "**技术栈：** Vue 3 + TypeScript + Element Plus + Pinia\\n\\n**主要功能：**\\n- 支持 1-3 级嵌套菜单配置\\n- 页面字段可视化配置\\n- 多种表单控件类型支持\\n- 动态数据页面渲染"}',
 true, 4, 12)
ON CONFLICT (id) DO NOTHING;

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
"""

DATA_FILES_DDL = """
CREATE TABLE IF NOT EXISTS data_files (
  id            VARCHAR(100) PRIMARY KEY,
  original_name TEXT NOT NULL,
  mime_type     TEXT,
  size_bytes    BIGINT NOT NULL,
  storage_path  TEXT NOT NULL,
  uploaded_by   VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
  uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_data_files_uploaded_at
  ON data_files(uploaded_at DESC);
"""

AI_CHAT_PROMPT_TEMPLATES_DDL = """
CREATE TABLE IF NOT EXISTS ai_chat_prompt_templates (
  id         VARCHAR(100) PRIMARY KEY,
  user_id    VARCHAR(100) NOT NULL REFERENCES users(id),
  name       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_chat_prompt_templates_user
  ON ai_chat_prompt_templates(user_id, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_template_user_name
  ON ai_chat_prompt_templates(user_id, name);
"""

AI_CHAT_BATCHES_DDL = """
CREATE TABLE IF NOT EXISTS ai_chat_batches (
  id          VARCHAR(100) PRIMARY KEY,
  user_id     VARCHAR(100) NOT NULL REFERENCES users(id),
  name        TEXT NOT NULL,
  prompt      TEXT NOT NULL,
  template_id VARCHAR(100) NULL REFERENCES ai_chat_prompt_templates(id) ON DELETE SET NULL,
  agent       TEXT,
  model       TEXT,
  provision_repo TEXT,
  provision_ref  TEXT,
  status      TEXT NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','running','completed','partial','failed')),
  total       INT  NOT NULL DEFAULT 0,
  done        INT  NOT NULL DEFAULT 0,
  failed      INT  NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at  TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_chat_batches_user_created
  ON ai_chat_batches(user_id, created_at DESC);
-- Idempotent upgrade: add `agent`/`model` to DBs created before they joined CREATE above.
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS agent TEXT;
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS model TEXT;
-- Per-batch workspace provisioning: clone an agent/skill repo into each child's
-- .opencode/ before its session starts (so project-level agents are usable).
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS provision_repo TEXT;
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS provision_ref TEXT;
"""

AI_CHAT_SESSIONS_BATCH_COLUMNS_DDL = """
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS batch_id         VARCHAR(100) NULL REFERENCES ai_chat_batches(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS batch_seq        INT  NULL,
  ADD COLUMN IF NOT EXISTS batch_input_file TEXT NULL,
  ADD COLUMN IF NOT EXISTS error_message         TEXT NULL,
  ADD COLUMN IF NOT EXISTS last_message_preview  TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_batch
  ON ai_chat_sessions(batch_id, batch_seq);
ALTER TABLE ai_chat_sessions
  ALTER COLUMN workspace_path DROP NOT NULL,
  ALTER COLUMN session_token DROP NOT NULL,
  ALTER COLUMN token_expires_at DROP NOT NULL;
"""

AI_SCAN_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS ai_scan_tasks (
  id              VARCHAR(100) PRIMARY KEY,
  name            TEXT NOT NULL,
  enabled         BOOLEAN NOT NULL DEFAULT TRUE,
  owner_user_id   VARCHAR(100) NOT NULL REFERENCES users(id),
  collection      VARCHAR(200) NOT NULL,
  branch_id       VARCHAR(100) NOT NULL DEFAULT 'main',
  status_field    TEXT NOT NULL,
  pending_value   TEXT NOT NULL DEFAULT '',
  running_value   TEXT NOT NULL DEFAULT '处理中',
  done_value      TEXT NOT NULL DEFAULT '已处理',
  failed_value    TEXT NOT NULL DEFAULT '处理失败',
  extra_filter    JSONB NOT NULL DEFAULT '{}'::jsonb,
  context_fields  JSONB NOT NULL DEFAULT '{}'::jsonb,
  prompt_template TEXT NOT NULL,
  field_mapping   JSONB NOT NULL DEFAULT '[]'::jsonb,
  schedule_interval_minutes INT NOT NULL DEFAULT 15,
  max_records_per_scan      INT NOT NULL DEFAULT 20,
  agent           TEXT,
  last_run_at     TIMESTAMPTZ,
  last_scan_count INT DEFAULT 0,
  last_error      TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS scan_task_id     VARCHAR(100) NULL REFERENCES ai_scan_tasks(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_record_id VARCHAR(100) NULL;
CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_scan
  ON ai_chat_sessions(scan_task_id, source_record_id);
-- Idempotent upgrade: add `agent` to DBs created before it joined the CREATE above.
ALTER TABLE ai_scan_tasks ADD COLUMN IF NOT EXISTS agent TEXT;
"""


RBAC_DDL = """
CREATE TABLE IF NOT EXISTS roles (
    id                  VARCHAR(100) PRIMARY KEY,
    name                VARCHAR(200) NOT NULL,
    description         TEXT,
    is_system           BOOLEAN NOT NULL DEFAULT FALSE,
    is_superuser        BOOLEAN NOT NULL DEFAULT FALSE,
    default_page_access VARCHAR(10) NOT NULL DEFAULT 'read'
                        CHECK (default_page_access IN ('none','read','write')),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id        VARCHAR(100) NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_key VARCHAR(100) NOT NULL,
    PRIMARY KEY (role_id, permission_key)
);

CREATE TABLE IF NOT EXISTS role_page_permissions (
    role_id     VARCHAR(100) NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    page_id     VARCHAR(100) NOT NULL,
    can_read    BOOLEAN NOT NULL DEFAULT TRUE,
    can_create  BOOLEAN NOT NULL DEFAULT FALSE,
    can_update  BOOLEAN NOT NULL DEFAULT FALSE,
    can_delete  BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (role_id, page_id)
);
"""


def _create_concurrent_index(index_name, create_sql):
    """建 CONCURRENTLY 索引，带失败自愈。独立 autocommit 连接（CONCURRENTLY 不能
    在事务块里跑）。

    CONCURRENTLY 构建中途失败/被打断会在 pg_index 里留一个 invalid 的索引
    占位；`IF NOT EXISTS` 只按名字判断存在与否、不检查有效性，不清理的话
    重跑 init_db.py 会一直跳过这个坏索引、永远不重建。这里先探测并清掉
    invalid 的残留，再尝试建；这次尝试本身失败也做同样的清理，让下一次
    重跑能自愈，不需要人工介入。
    """
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        # to_regclass() 走 psycopg2 取回的是 regclass 的文本表示（对象名），不是
        # 原始 oid 整数；直接把它当 oid 参数再传一次会报 InvalidTextRepresentation。
        # 让 Postgres 在同一条查询里把 to_regclass() 的结果直接和 indexrelid 比较，
        # 不经过客户端往返，类型转换交给服务端处理；索引不存在时 to_regclass()
        # 返回 NULL，WHERE 条件天然不匹配，fetchone() 拿到 None，语义清晰。
        cur.execute(
            'SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(%s)',
            (index_name,),
        )
        row = cur.fetchone()
        if row is not None and not row[0]:
            print(f"Found invalid leftover index {index_name}, dropping before rebuild.")
            cur.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {index_name}')
        cur.execute(create_sql)
        print(f"Index {index_name} ready.")
    except Exception:
        try:
            cur.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {index_name}')
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _create_search_text_trgm_index():
    _create_concurrent_index(
        'idx_dynamic_data_search_trgm',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dynamic_data_search_trgm '
        'ON dynamic_data USING gin (search_text gin_trgm_ops)'
    )


def _create_search_text_pending_index():
    """自维护的"待回填"清单：只装 search_text 仍是 NULL 的行。

    没有这个索引时，_backfill_search_text 在已经全部回填完的大 collection
    上每次重跑都要扫一遍该 collection 的全部行才能确认"没有剩余"——千万级
    下每次 `git pull` 后重跑 init_db.py 都白白花这个代价。部分索引只覆盖
    还没处理的行，回填完 Postgres 自动把它从索引里摘掉（部分索引的维护
    是自动的，不需要额外记账），重跑的代价随剩余行数趋近于零。副作用是
    它对任何忘记盖 search_text 的写路径（不只是这次改的几个）也天然兜底：
    那些行会自动出现在索引里，下次跑 init_db.py 就会被捞去补算。
    """
    _create_concurrent_index(
        'idx_dynamic_data_search_text_pending',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dynamic_data_search_text_pending '
        'ON dynamic_data (collection) WHERE search_text IS NULL'
    )


def _backfill_search_text(conn, batch_size=2000):
    """为写路径改造前就存在的历史行补算 search_text（新行由写路径直接维护）。

    按 collection 分批处理，每批 UPDATE 后立即 commit，避免千万级存量数据
    一次性锁住整张表或撑爆内存。search_text 写完后该行不再匹配 IS NULL，
    循环天然终止，可安全重复执行（幂等）；配合 idx_dynamic_data_search_text_pending
    分区索引，已回填完的 collection 重跑代价接近于零。
    """
    cur = conn.cursor()
    cur.execute('SELECT id, fields FROM page_configs')
    page_rows = cur.fetchall()
    total_updated = 0
    for page_id, fields in page_rows:
        if not page_id.startswith('page-'):
            continue
        collection = page_id[len('page-'):]
        while True:
            cur.execute(
                'SELECT id, branch_id, data FROM dynamic_data '
                'WHERE collection = %s AND search_text IS NULL LIMIT %s',
                (collection, batch_size),
            )
            rows = cur.fetchall()
            if not rows:
                break
            for rid, branch_id, data in rows:
                search_text = compute_search_text(data or {}, fields or [])
                cur.execute(
                    'UPDATE dynamic_data SET search_text = %s WHERE id = %s AND branch_id = %s',
                    (search_text, rid, branch_id),
                )
            conn.commit()
            total_updated += len(rows)
    if total_updated:
        print(f"Backfilled search_text for {total_updated} existing dynamic_data row(s).")


def init_db():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()

        # Create tables
        cur.execute(DDL)
        conn.commit()
        print("Tables created.")

        # Keyword search acceleration: search_text 预计算列 + pg_trgm GIN 索引
        # （见 utils/search_text.py）。索引用独立 autocommit 连接并发建，
        # 存量数据补算走批量 backfill，均可在已有数据的库上安全重复执行。
        _create_search_text_trgm_index()
        _create_search_text_pending_index()
        _backfill_search_text(conn)

        # Data-page file/image field storage (replaces blob: URLs)
        cur.execute(DATA_FILES_DDL)
        conn.commit()
        print("Data files table created.")

        # Create AI Chat batch-related tables (Task 1)
        cur.execute(AI_CHAT_PROMPT_TEMPLATES_DDL)
        conn.commit()
        print("AI chat prompt templates table created.")

        cur.execute(AI_CHAT_BATCHES_DDL)
        conn.commit()
        print("AI chat batches table created.")

        cur.execute(AI_CHAT_SESSIONS_BATCH_COLUMNS_DDL)
        conn.commit()
        print("AI chat sessions batch columns added.")

        cur.execute(AI_SCAN_TASKS_DDL)
        conn.commit()
        print("ai_scan_tasks table + ai_chat_sessions scan columns created.")

        # RBAC custom roles (Phase 0)
        cur.execute(RBAC_DDL)
        conn.commit()
        print("RBAC tables (roles, role_permissions, role_page_permissions) created.")

        # Migration: drop the hardcoded role CHECK so custom roles are allowed.
        # The constraint name is auto-generated; find and drop it dynamically.
        cur.execute("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'users'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%role%'
        """)
        for (conname,) in cur.fetchall():
            cur.execute(f'ALTER TABLE users DROP CONSTRAINT IF EXISTS "{conname}"')
        conn.commit()
        print("Dropped hardcoded users.role CHECK constraint (if present).")

        # Migrations: add roles column if missing
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'menus' AND column_name = 'roles'
        """)
        if not cur.fetchone():
            cur.execute("""ALTER TABLE menus ADD COLUMN roles JSONB NOT NULL DEFAULT '["admin","developer","guest"]'::jsonb""")
            conn.commit()
            print("Added roles column to menus table.")

        # Migration: add operation log menu if missing
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-4'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                "VALUES ('menu-3-4', %s, 'Tickets', NULL, 'menu-3', 4, '/admin/operation-log', %s)",
                ('操作日志', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added operation log menu.")

        # Migration: add backup menu if missing
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-5'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                "VALUES ('menu-3-5', %s, 'FolderOpened', NULL, 'menu-3', 5, '/admin/backup', %s)",
                ('系统备份', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added backup menu.")

        # Migration: add export_scripts column to page_configs if missing
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'page_configs' AND column_name = 'export_scripts'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE page_configs ADD COLUMN export_scripts JSONB DEFAULT '[]'::jsonb")
            conn.commit()
            print("Added export_scripts column to page_configs table.")

        # Migration: add batch_id / batch_desc columns to operation_logs
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'operation_logs' AND column_name = 'batch_id'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE operation_logs ADD COLUMN batch_id VARCHAR(100)")
            cur.execute("ALTER TABLE operation_logs ADD COLUMN batch_desc VARCHAR(500)")
            cur.execute("CREATE INDEX idx_operation_logs_batch_id ON operation_logs(batch_id)")
            conn.commit()
            print("Added batch_id/batch_desc columns to operation_logs table.")

        # Migration: add scope column to export_scripts
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'export_scripts' AND column_name = 'scope'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE export_scripts ADD COLUMN scope VARCHAR(50) NOT NULL DEFAULT 'page'")
            conn.commit()
            print("Added scope column to export_scripts table.")

        # Migration: add binding columns to export_scripts (bound_collection / bound_menu_id)
        for col in ('bound_collection', 'bound_menu_id'):
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'export_scripts' AND column_name = %s
            """, (col,))
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE export_scripts ADD COLUMN {col} VARCHAR(100)")
                conn.commit()
                print(f"Added {col} column to export_scripts table.")

        # Migration: add row_export_scripts column to page_configs
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'page_configs' AND column_name = 'row_export_scripts'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE page_configs ADD COLUMN row_export_scripts JSONB DEFAULT '[]'::jsonb")
            conn.commit()
            print("Added row_export_scripts column to page_configs table.")

        # Migration: add export scripts menu if missing
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-6'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                "VALUES ('menu-3-6', %s, 'Promotion', NULL, 'menu-3', 6, '/admin/export-scripts', %s)",
                ('导出脚本', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added export scripts menu.")

        # Migration: add api_public column to page_configs
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'page_configs' AND column_name = 'api_public'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE page_configs ADD COLUMN api_public BOOLEAN NOT NULL DEFAULT FALSE")
            conn.commit()
            print("Added api_public column to page_configs table.")

        # Migration: add api_writable column to page_configs
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'page_configs' AND column_name = 'api_writable'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE page_configs ADD COLUMN api_writable BOOLEAN NOT NULL DEFAULT FALSE")
            conn.commit()
            print("Added api_writable column to page_configs table.")

        # Migration: add validation_script column to page_configs
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'page_configs' AND column_name = 'validation_script'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE page_configs ADD COLUMN validation_script TEXT")
            conn.commit()
            print("Added validation_script column to page_configs table.")

        # Migration: add Open API menu if missing
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-7'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                "VALUES ('menu-3-7', %s, 'Key', NULL, 'menu-3', 7, '/admin/api-keys', %s)",
                ('Open API', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added Open API menu.")

        # Migration: add validation scripts menu if missing
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-8'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                "VALUES ('menu-3-8', %s, 'CircleCheck', NULL, 'menu-3', 8, '/admin/validation-scripts', %s)",
                ('校验脚本', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added validation scripts menu.")

        # Migration: reorganize system config menus into sub-groups
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-a'")
        if not cur.fetchone():
            # 插入 3 个分组容器菜单
            for mid, name, icon, order in [
                ('menu-3-a', '平台管理', 'Platform', 1),
                ('menu-3-b', '数据工具', 'DataLine', 2),
                ('menu-3-c', '系统运维', 'Monitor',  3),
            ]:
                cur.execute(
                    'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                    'VALUES (%s, %s, %s, NULL, %s, %s, NULL, %s)',
                    (mid, name, icon, 'menu-3', order, psycopg2.extras.Json(['admin'])),
                )

            # 将现有菜单移入分组，同时重置 order
            reparent = [
                # 平台管理
                ('menu-3-1', 'menu-3-a', 1),
                ('menu-3-2', 'menu-3-a', 2),
                ('menu-3-3', 'menu-3-a', 3),
                # 数据工具
                ('menu-3-6', 'menu-3-b', 1),
                ('menu-3-8', 'menu-3-b', 2),
                ('menu-3-7', 'menu-3-b', 3),
                # 系统运维
                ('menu-3-4', 'menu-3-c', 1),
                ('menu-3-5', 'menu-3-c', 2),
            ]
            for menu_id, new_parent, new_order in reparent:
                cur.execute(
                    'UPDATE menus SET parent_id = %s, "order" = %s WHERE id = %s',
                    (new_parent, new_order, menu_id),
                )
            conn.commit()
            print("Reorganized system config menus into sub-groups.")

        # Migration: add ETL management menu if missing
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-9'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                "VALUES ('menu-3-9', %s, 'Connection', NULL, 'menu-3-b', 4, '/admin/etl-tasks', %s)",
                ('ETL 管理', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added ETL management menu.")

        # Migration: add Query Console menu if missing
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-10'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                "VALUES ('menu-3-10', %s, 'Search', NULL, 'menu-3-b', 5, '/admin/query', %s)",
                ('数据查询', psycopg2.extras.Json(['admin', 'developer'])),
            )
            conn.commit()
            print("Added query console menu.")

        # Migration: move 数据工具 to top-level, move Open API to 平台管理
        cur.execute("SELECT parent_id FROM menus WHERE id = 'menu-3-b'")
        row = cur.fetchone()
        if row and row[0] == 'menu-3':
            # 数据工具 → 一级菜单 (order=3), 系统配置 → order=4
            cur.execute('UPDATE menus SET parent_id = NULL, "order" = 3, roles = %s WHERE id = %s',
                        (psycopg2.extras.Json(['admin', 'developer']), 'menu-3-b'))
            cur.execute('UPDATE menus SET "order" = 4 WHERE id = %s', ('menu-3',))
            # Open API → 平台管理
            cur.execute('UPDATE menus SET parent_id = %s, "order" = 4 WHERE id = %s',
                        ('menu-3-a', 'menu-3-7'))
            # 系统运维 order 调整 (原 order=3, 改为 2，因为数据工具已移走)
            cur.execute('UPDATE menus SET "order" = 2 WHERE id = %s', ('menu-3-c',))
            conn.commit()
            print("Moved 数据工具 to top-level menu, moved Open API to 平台管理.")

        # Migration: add version and updated_at columns to dynamic_data
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'dynamic_data' AND column_name = 'version'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE dynamic_data ADD COLUMN updated_at TIMESTAMPTZ DEFAULT NOW()")
            cur.execute("ALTER TABLE dynamic_data ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
            conn.commit()
            print("Added version and updated_at columns to dynamic_data table.")

        # Migration: add view_config column to page_configs
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'page_configs' AND column_name = 'view_config'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE page_configs ADD COLUMN view_config JSONB DEFAULT '{}'::jsonb")
            conn.commit()
            print("Added view_config column to page_configs table.")

        # Migration: add delete_binding column to page_configs
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'page_configs' AND column_name = 'delete_binding'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE page_configs ADD COLUMN delete_binding JSONB")
            conn.commit()
            print("Added delete_binding column to page_configs table.")

        # Migration: add field_changes column to operation_logs
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'operation_logs' AND column_name = 'field_changes'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE operation_logs ADD COLUMN field_changes JSONB")
            conn.commit()
            print("Added field_changes column to operation_logs table.")

        # Migration: create record_comments table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'record_comments'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE record_comments (
                    id              VARCHAR(100) PRIMARY KEY,
                    collection      VARCHAR(200) NOT NULL,
                    record_id       VARCHAR(100) NOT NULL,
                    content         TEXT NOT NULL,
                    mentions        JSONB DEFAULT '[]'::jsonb,
                    author_id       VARCHAR(100) NOT NULL,
                    author_name     VARCHAR(200) NOT NULL,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX idx_comments_record ON record_comments(collection, record_id);
                CREATE INDEX idx_comments_created ON record_comments(created_at DESC);
            """)
            conn.commit()
            print("Created record_comments table.")

        # Migration: create dashboards table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'dashboards'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE dashboards (
                    id              VARCHAR(100) PRIMARY KEY,
                    name            VARCHAR(200) NOT NULL,
                    description     TEXT,
                    layout          JSONB NOT NULL DEFAULT '[]'::jsonb,
                    owner_id        VARCHAR(100),
                    is_global       BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            conn.commit()
            print("Created dashboards table.")

        # Migration: create notifications table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'notifications'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE notifications (
                    id              VARCHAR(100) PRIMARY KEY,
                    user_id         VARCHAR(100) NOT NULL,
                    type            VARCHAR(50) NOT NULL,
                    title           VARCHAR(500) NOT NULL,
                    content         TEXT,
                    source_collection VARCHAR(200),
                    source_record_id  VARCHAR(100),
                    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX idx_notifications_user ON notifications(user_id, is_read, created_at DESC);
            """)
            conn.commit()
            print("Created notifications table.")

        # Migration: create reminders table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'reminders'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE reminders (
                    id              VARCHAR(100) PRIMARY KEY,
                    collection      VARCHAR(200) NOT NULL,
                    record_id       VARCHAR(100) NOT NULL,
                    user_id         VARCHAR(100) NOT NULL,
                    remind_at       TIMESTAMPTZ NOT NULL,
                    message         VARCHAR(500),
                    is_sent         BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX idx_reminders_pending ON reminders(is_sent, remind_at);
            """)
            conn.commit()
            print("Created reminders table.")

        # Migration: create trigger_rules table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'trigger_rules'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE trigger_rules (
                    id              VARCHAR(100) PRIMARY KEY,
                    name            VARCHAR(200) NOT NULL,
                    description     TEXT,
                    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
                    source_collection VARCHAR(200) NOT NULL,
                    trigger_event   VARCHAR(50) NOT NULL,
                    trigger_condition JSONB DEFAULT '{}'::jsonb,
                    target_collection VARCHAR(200) NOT NULL,
                    action_type     VARCHAR(50) NOT NULL,
                    action_config   JSONB NOT NULL DEFAULT '{}'::jsonb,
                    execution_order INTEGER DEFAULT 0,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX idx_trigger_rules_source ON trigger_rules(source_collection, enabled);
            """)
            conn.commit()
            print("Created trigger_rules table.")

        # Migration: create trigger_logs table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'trigger_logs'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE trigger_logs (
                    id              VARCHAR(100) PRIMARY KEY,
                    rule_id         VARCHAR(100) NOT NULL,
                    rule_name       VARCHAR(200),
                    source_collection VARCHAR(200),
                    source_record_id  VARCHAR(100),
                    target_collection VARCHAR(200),
                    target_record_id  VARCHAR(100),
                    status          VARCHAR(50) NOT NULL,
                    error_message   TEXT,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX idx_trigger_logs_rule ON trigger_logs(rule_id, created_at DESC);
            """)
            conn.commit()
            print("Created trigger_logs table.")

        # Migration: create ai_settings table if missing
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'ai_settings'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE ai_settings (
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
                INSERT INTO ai_settings (id) VALUES (1);
            """)
            conn.commit()
            print("Created ai_settings table.")

        # Migration: create system_config table if missing
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'system_config'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE system_config (
                    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                    system_name     VARCHAR(200) NOT NULL DEFAULT '巡检用例管理系统',
                    system_short_name VARCHAR(50) NOT NULL DEFAULT '巡检管理',
                    logo_url        VARCHAR(500),
                    updated_at      TIMESTAMPTZ DEFAULT NOW(),
                    updated_by      VARCHAR(100)
                );
                INSERT INTO system_config (id) VALUES (1);
            """)
            conn.commit()
            print("Created system_config table.")

        # Migration: add login-page copy columns to system_config (idempotent)
        cur.execute("""
            ALTER TABLE system_config
                ADD COLUMN IF NOT EXISTS login_title    VARCHAR(200),
                ADD COLUMN IF NOT EXISTS login_subtitle VARCHAR(300),
                ADD COLUMN IF NOT EXISTS login_footer   VARCHAR(500);
        """)
        conn.commit()

        # Migration: create home_widgets table if missing
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'home_widgets'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE home_widgets (
                    id              VARCHAR(100) PRIMARY KEY,
                    widget_type     VARCHAR(50) NOT NULL,
                    title           VARCHAR(200),
                    content         JSONB,
                    enabled         BOOLEAN DEFAULT TRUE,
                    "order"         INTEGER DEFAULT 0,
                    visible_roles   JSONB DEFAULT '["admin","developer","guest"]',
                    layout_x        INTEGER NOT NULL DEFAULT 0,
                    layout_y        INTEGER NOT NULL DEFAULT 0,
                    layout_w        INTEGER NOT NULL DEFAULT 12,
                    layout_h        INTEGER NOT NULL DEFAULT 4,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            # Insert default widgets
            cur.execute("""
                INSERT INTO home_widgets (id, widget_type, title, content, enabled, "order", layout_y) VALUES
                ('welcome', 'welcome', '欢迎',
                 '{"heading": "欢迎使用巡检用例管理系统", "description": "本系统支持动态配置菜单和页面，实现灵活的数据管理。"}',
                 true, 1, 0),
                ('stats', 'stats', '系统概览',
                 '{"items": [{"type": "menuCount", "label": "菜单数量", "icon": "Document"}, {"type": "pageCount", "label": "页面配置", "icon": "Files"}, {"type": "fieldCount", "label": "字段配置", "icon": "Setting"}]}',
                 true, 2, 4),
                ('quick-links', 'quick-links', '快捷入口',
                 '{"links": [{"name": "菜单管理", "path": "/admin/menu", "icon": "Menu"}, {"name": "页面配置", "path": "/admin/page-config", "icon": "Files"}, {"name": "批量导出", "path": "", "icon": "Download", "action": "batchExport"}]}',
                 true, 3, 8),
                ('system-info', 'system-info', '系统说明',
                 '{"markdown": "**技术栈：** Vue 3 + TypeScript + Element Plus + Pinia\\n\\n**主要功能：**\\n- 支持 1-3 级嵌套菜单配置\\n- 页面字段可视化配置\\n- 多种表单控件类型支持\\n- 动态数据页面渲染"}',
                 true, 4, 12)
            """)
            conn.commit()
            print("Created home_widgets table with default widgets.")

        # Migration: add layout_x/y/w/h columns to home_widgets (grid layout editor)
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'home_widgets' AND column_name = 'layout_x'
        """)
        if not cur.fetchone():
            cur.execute("""
                ALTER TABLE home_widgets
                    ADD COLUMN layout_x INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN layout_y INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN layout_w INTEGER NOT NULL DEFAULT 12,
                    ADD COLUMN layout_h INTEGER NOT NULL DEFAULT 4;
            """)
            # Backfill: preserve today's vertical stacking order as full-width rows
            # (x=0, w=12, h=4 from the column DEFAULTs above; only y needs computing)
            cur.execute('SELECT id FROM home_widgets ORDER BY "order"')
            ids_in_order = [row[0] for row in cur.fetchall()]
            for idx, widget_id in enumerate(ids_in_order):
                cur.execute(
                    'UPDATE home_widgets SET layout_y = %s WHERE id = %s',
                    (idx * 4, widget_id)
                )
            conn.commit()
            print("Added layout_x/y/w/h columns to home_widgets and backfilled positions.")

        # Migration: add backup_scope and backup_tables columns to backups
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'backups' AND column_name = 'backup_scope'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE backups ADD COLUMN backup_scope VARCHAR(20) DEFAULT 'full'")
            cur.execute("ALTER TABLE backups ADD COLUMN backup_tables JSONB DEFAULT '[]'::jsonb")
            conn.commit()
            print("Added backup_scope and backup_tables columns to backups table.")

        # Migration: create collection_versions table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'collection_versions'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE collection_versions (
                    id              VARCHAR(100) PRIMARY KEY,
                    collection      VARCHAR(200) NOT NULL,
                    name            VARCHAR(200) NOT NULL,
                    description     TEXT,
                    version_type    VARCHAR(20) NOT NULL DEFAULT 'snapshot',
                    parent_version  VARCHAR(100),
                    status          VARCHAR(20) NOT NULL DEFAULT 'active',
                    data_hash       VARCHAR(64),
                    records_count   INTEGER DEFAULT 0,
                    relations_count INTEGER DEFAULT 0,
                    created_by      VARCHAR(200),
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    merged_at       TIMESTAMPTZ,
                    merged_by       VARCHAR(200),
                    merged_into     VARCHAR(100),
                    initialized_at  TIMESTAMPTZ,
                    is_protected    BOOLEAN NOT NULL DEFAULT FALSE,
                    FOREIGN KEY (parent_version) REFERENCES collection_versions(id) ON DELETE SET NULL
                );
                CREATE INDEX idx_cv_collection ON collection_versions(collection);
                CREATE INDEX idx_cv_parent ON collection_versions(parent_version);
                CREATE INDEX idx_cv_status ON collection_versions(status);
            """)
            conn.commit()
            print("Created collection_versions table.")

        # Migration: create version_snapshots table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'version_snapshots'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE version_snapshots (
                    version_id      VARCHAR(100) NOT NULL,
                    record_id       VARCHAR(100) NOT NULL,
                    record_data     JSONB NOT NULL,
                    created_at      TIMESTAMPTZ,
                    PRIMARY KEY (version_id, record_id),
                    FOREIGN KEY (version_id) REFERENCES collection_versions(id) ON DELETE CASCADE
                );
                CREATE INDEX idx_vs_version ON version_snapshots(version_id);
            """)
            conn.commit()
            print("Created version_snapshots table.")

        # Migration: create version_relations table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'version_relations'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE version_relations (
                    version_id          VARCHAR(100) NOT NULL,
                    collection          VARCHAR(200) NOT NULL,
                    record_id           VARCHAR(100) NOT NULL,
                    field_name          VARCHAR(200) NOT NULL,
                    related_collection  VARCHAR(200) NOT NULL,
                    related_id          VARCHAR(100) NOT NULL,
                    PRIMARY KEY (version_id, collection, record_id, field_name, related_id),
                    FOREIGN KEY (version_id) REFERENCES collection_versions(id) ON DELETE CASCADE
                );
            """)
            conn.commit()
            print("Created version_relations table.")

        # Migration: add branch_id to dynamic_data and change primary key
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'dynamic_data' AND column_name = 'branch_id'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE dynamic_data ADD COLUMN branch_id VARCHAR(100) NOT NULL DEFAULT 'main'")
            # Drop the existing primary key and create a composite one
            cur.execute("ALTER TABLE dynamic_data DROP CONSTRAINT IF EXISTS dynamic_data_pkey")
            cur.execute("ALTER TABLE dynamic_data ADD PRIMARY KEY (id, branch_id)")
            conn.commit()
            print("Added branch_id column to dynamic_data table and updated primary key.")

        # Migration: add branch_id to data_relations
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'data_relations' AND column_name = 'branch_id'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE data_relations ADD COLUMN branch_id VARCHAR(100) NOT NULL DEFAULT 'main'")
            conn.commit()
            print("Added branch_id column to data_relations table.")

        # Migration: update data_relations primary key to include branch_id
        cur.execute("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'data_relations'::regclass AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)
        """)
        pk_cols = [row[0] for row in cur.fetchall()]
        if 'branch_id' not in pk_cols:
            cur.execute("ALTER TABLE data_relations DROP CONSTRAINT IF EXISTS data_relations_pkey")
            cur.execute("ALTER TABLE data_relations ADD PRIMARY KEY (collection, record_id, field_name, related_id, branch_id)")
            conn.commit()
            print("Updated data_relations primary key to include branch_id.")

        # Migration: ensure dynamic_data primary key includes branch_id
        cur.execute("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'dynamic_data'::regclass AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)
        """)
        dynamic_pk_cols = [row[0] for row in cur.fetchall()]
        if dynamic_pk_cols != ['id', 'branch_id']:
            cur.execute("ALTER TABLE dynamic_data DROP CONSTRAINT IF EXISTS dynamic_data_pkey")
            cur.execute("ALTER TABLE dynamic_data ADD PRIMARY KEY (id, branch_id)")
            conn.commit()
            print("Updated dynamic_data primary key to (id, branch_id).")

        # Migration: create user_current_branch table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'user_current_branch'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE user_current_branch (
                    id              VARCHAR(100) PRIMARY KEY,
                    user_id         VARCHAR(100) NOT NULL,
                    username        VARCHAR(100) NOT NULL,
                    collection      VARCHAR(200) NOT NULL,
                    branch_id       VARCHAR(100) NOT NULL DEFAULT 'main',
                    updated_at      TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, collection)
                );
                CREATE INDEX idx_user_current_branch_user ON user_current_branch(user_id);
                CREATE INDEX idx_user_current_branch_collection ON user_current_branch(collection);
            """)
            conn.commit()
            print("Created user_current_branch table.")

        # Migration: add initialized_at column to collection_versions
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'collection_versions' AND column_name = 'initialized_at'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE collection_versions ADD COLUMN initialized_at TIMESTAMPTZ")
            conn.commit()
            print("Added initialized_at column to collection_versions table.")

        # Migration: add branch_id column to operation_logs
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'operation_logs' AND column_name = 'branch_id'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE operation_logs ADD COLUMN branch_id VARCHAR(100) DEFAULT 'main'")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_operation_logs_branch_id ON operation_logs(branch_id)")
            conn.commit()
            print("Added branch_id column to operation_logs table.")

        # Migration: add export_script_id column to menus for menu-level export
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'menus' AND column_name = 'export_script_id'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE menus ADD COLUMN export_script_id VARCHAR(100)")
            conn.commit()
            print("Added export_script_id column to menus table.")

        # Migration: add menu export page menu entry
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-12'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                "VALUES ('menu-3-12', %s, 'Download', NULL, 'menu-3-b', 6, '/admin/menu-export', %s)",
                ('数据导出', psycopg2.extras.Json(['admin', 'developer'])),
            )
            conn.commit()
            print("Added menu export page menu.")

        # Migration: add menu_type and project_id columns to menus
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'menus' AND column_name = 'menu_type'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE menus ADD COLUMN menu_type VARCHAR(20) NOT NULL DEFAULT 'data'")
            cur.execute("ALTER TABLE menus ADD COLUMN project_id VARCHAR(100) NULL")
            cur.execute("CREATE INDEX idx_menus_menu_type ON menus(menu_type)")
            cur.execute("CREATE INDEX idx_menus_project_id ON menus(project_id)")
            conn.commit()
            print("Added menu_type and project_id columns to menus table.")

            # Update existing menus with correct menu_type
            # System menus (首页、仪表盘、数据工具、系统配置)
            system_menu_ids = [
                'menu-1', 'menu-dashboard',  # 首页、仪表盘
                'menu-3-b', 'menu-3-6', 'menu-3-8', 'menu-3-9', 'menu-3-10', 'menu-3-12',  # 数据工具及其子项
                'menu-3', 'menu-3-a', 'menu-3-c',  # 系统配置分组
                'menu-3-1', 'menu-3-2', 'menu-3-3', 'menu-3-7', 'menu-3-11', 'menu-3-13', 'menu-3-14',  # 平台管理子项
                'menu-3-4', 'menu-3-5',  # 系统运维子项
            ]
            for mid in system_menu_ids:
                cur.execute("UPDATE menus SET menu_type = 'system' WHERE id = %s", (mid,))

            # 巡检管理及其子项是示例数据，标记为 workspace/project/data（待后续删除或重新分类）
            # menu-2 及其子项暂时标记为 system，后续可根据需求调整
            workspace_menu_ids = ['menu-2']
            project_menu_ids = ['menu-2-1', 'menu-2-2', 'menu-2-3']
            data_menu_ids = ['menu-2-3-1', 'menu-2-3-2']

            for mid in workspace_menu_ids:
                cur.execute("UPDATE menus SET menu_type = 'workspace' WHERE id = %s", (mid,))
            for mid in project_menu_ids:
                cur.execute("UPDATE menus SET menu_type = 'project' WHERE id = %s", (mid,))
            for mid in data_menu_ids:
                cur.execute("UPDATE menus SET menu_type = 'data' WHERE id = %s", (mid,))

            conn.commit()
            print("Updated menu_type for existing menus.")

        # Migration: add AI settings, Webhook, and SystemSettings menus if missing
        # These must run AFTER menu_type column is added
        for menu_id, name, icon, parent, order, path in [
            ('menu-3-11', 'AI 配置', 'MagicStick', 'menu-3-a', 5, '/admin/ai-settings'),
            ('menu-3-13', 'Webhook', 'Link', 'menu-3-a', 6, '/admin/webhook-settings'),
            ('menu-3-14', '系统设置', 'Setting', 'menu-3-a', 7, '/admin/system-settings'),
        ]:
            cur.execute("SELECT id FROM menus WHERE id = %s", (menu_id,))
            if not cur.fetchone():
                cur.execute(
                    'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                    'VALUES (%s, %s, %s, NULL, %s, %s, %s, %s, %s)',
                    (menu_id, name, icon, parent, order, path, psycopg2.extras.Json(['admin']), 'system'),
                )
                conn.commit()
                print(f"Added {name} menu.")

        # Migration: add 角色权限 (custom roles RBAC) menu if missing
        # Note: menu-3-12 is already taken by the 数据导出 (menu-export) migration above,
        # so the role management menu uses the next free id menu-3-15.
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-15'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                "VALUES ('menu-3-15', %s, 'Lock', NULL, 'menu-3-a', 8, '/admin/roles', %s, 'system')",
                ('角色权限', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added 角色权限 menu.")

        # Migration: add AI 定时任务 (scheduled AI row-processor) menu if missing
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-16'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                "VALUES ('menu-3-16', %s, 'AlarmClock', NULL, 'menu-3-b', 7, '/admin/ai-scan-tasks', %s, 'system')",
                ('AI 定时任务', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added AI 定时任务 menu.")

        # === 设置中心：塌缩旧管理菜单树为单一"设置中心" ===
        # 旧 menu-3(系统配置)/menu-3-b(数据工具) 及其全部后代 → 删除；插入单一 menu-settings。
        # 这一步在所有 menu-3* 旧块之后运行，使用 init_db 自身的 cur（可见未提交的插入），
        # 全新安装"先建后塌缩"，最终收敛为只剩 menu-settings。
        cur.execute(
            """
            WITH RECURSIVE sub AS (
                SELECT id FROM menus WHERE id IN ('menu-3', 'menu-3-b')
                UNION ALL
                SELECT m.id FROM menus m JOIN sub ON m.parent_id = sub.id
            )
            DELETE FROM menus WHERE id IN (SELECT id FROM sub)
            """
        )
        cur.execute("DELETE FROM menus WHERE id = 'menu-settings'")
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
            "VALUES ('menu-settings', '设置中心', 'Setting', NULL, NULL, 4, '/admin', %s, %s)",
            (psycopg2.extras.Json(['admin']), 'system'),
        )
        conn.commit()
        print("Collapsed legacy admin menu tree into 设置中心 (menu-settings).")

        # Migration: create project_versions table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'project_versions'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE project_versions (
                    id              VARCHAR(100) PRIMARY KEY,
                    project_menu_id VARCHAR(100) NOT NULL,
                    name            VARCHAR(200) NOT NULL,
                    description     TEXT,
                    version_type    VARCHAR(20) NOT NULL DEFAULT 'branch',
                    parent_version  VARCHAR(100),
                    status          VARCHAR(20) NOT NULL DEFAULT 'active',
                    created_by      VARCHAR(200),
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    merged_at       TIMESTAMPTZ,
                    merged_by       VARCHAR(200),
                    is_protected    BOOLEAN NOT NULL DEFAULT FALSE,
                    records_count   INTEGER DEFAULT 0,
                    initialized_at  TIMESTAMPTZ,
                    FOREIGN KEY (parent_version) REFERENCES project_versions(id) ON DELETE SET NULL
                );
                CREATE INDEX idx_pv_project ON project_versions(project_menu_id);
                CREATE INDEX idx_pv_status ON project_versions(status);
            """)
            conn.commit()
            print("Created project_versions table.")

        # Migration: add missing columns to project_versions
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'project_versions' AND column_name = 'records_count'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE project_versions ADD COLUMN records_count INTEGER DEFAULT 0")
            conn.commit()
            print("Added records_count column to project_versions.")

        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'project_versions' AND column_name = 'initialized_at'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE project_versions ADD COLUMN initialized_at TIMESTAMPTZ")
            conn.commit()
            print("Added initialized_at column to project_versions.")

        # Migration: add lock columns to project_versions
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'project_versions' AND column_name = 'is_locked'
        """)
        if not cur.fetchone():
            cur.execute("""
                ALTER TABLE project_versions
                ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN locked_at TIMESTAMPTZ,
                ADD COLUMN locked_by VARCHAR(200)
            """)
            conn.commit()
            print("Added lock columns (is_locked, locked_at, locked_by) to project_versions.")

        # Migration: add main branch lock to menus (project type)
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'menus' AND column_name = 'is_main_locked'
        """)
        if not cur.fetchone():
            cur.execute("""
                ALTER TABLE menus
                ADD COLUMN is_main_locked BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN main_locked_at TIMESTAMPTZ,
                ADD COLUMN main_locked_by VARCHAR(200)
            """)
            conn.commit()
            print("Added main branch lock columns to menus table.")

        # Migration: create merge_records table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'merge_records'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE merge_records (
                    id              VARCHAR(100) PRIMARY KEY,
                    source_version_id VARCHAR(100) NOT NULL,
                    source_version_name VARCHAR(200),
                    target_branch_id VARCHAR(100) NOT NULL,
                    target_branch_name VARCHAR(200),
                    project_menu_id VARCHAR(100) NOT NULL,
                    strategy        VARCHAR(20) NOT NULL,
                    merged_by       VARCHAR(200) NOT NULL,
                    merged_at       TIMESTAMPTZ DEFAULT NOW(),
                    records_created INTEGER DEFAULT 0,
                    records_updated INTEGER DEFAULT 0,
                    records_deleted INTEGER DEFAULT 0,
                    description     TEXT,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX idx_merge_source ON merge_records(source_version_id);
                CREATE INDEX idx_merge_target ON merge_records(target_branch_id);
                CREATE INDEX idx_merge_project ON merge_records(project_menu_id);
            """)
            conn.commit()
            print("Created merge_records table.")

        # Migration: create user_current_project_branch table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'user_current_project_branch'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE user_current_project_branch (
                    id              VARCHAR(100) PRIMARY KEY,
                    user_id         VARCHAR(100) NOT NULL,
                    username        VARCHAR(100) NOT NULL,
                    project_menu_id VARCHAR(100) NOT NULL,
                    branch_id       VARCHAR(100) NOT NULL DEFAULT 'main',
                    updated_at      TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, project_menu_id)
                );
                CREATE INDEX idx_ucpb_user ON user_current_project_branch(user_id);
                CREATE INDEX idx_ucpb_project ON user_current_project_branch(project_menu_id);
            """)
            conn.commit()
            print("Created user_current_project_branch table.")

        # Migration: create project_version_snapshots table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'project_version_snapshots'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE project_version_snapshots (
                    version_id      VARCHAR(100) NOT NULL,
                    collection      VARCHAR(200) NOT NULL,
                    record_id       VARCHAR(100) NOT NULL,
                    record_data     JSONB NOT NULL,
                    created_at      TIMESTAMPTZ,
                    PRIMARY KEY (version_id, collection, record_id),
                    FOREIGN KEY (version_id) REFERENCES project_versions(id) ON DELETE CASCADE
                );
                CREATE INDEX idx_pvs_version ON project_version_snapshots(version_id);
            """)
            conn.commit()
            print("Created project_version_snapshots table.")

        # Migration: create project_version_relations table
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'project_version_relations'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE project_version_relations (
                    version_id          VARCHAR(100) NOT NULL,
                    collection          VARCHAR(200) NOT NULL,
                    record_id           VARCHAR(100) NOT NULL,
                    field_name          VARCHAR(200) NOT NULL,
                    related_collection  VARCHAR(200) NOT NULL,
                    related_id          VARCHAR(100) NOT NULL,
                    PRIMARY KEY (version_id, collection, record_id, field_name, related_id),
                    FOREIGN KEY (version_id) REFERENCES project_versions(id) ON DELETE CASCADE
                );
                CREATE INDEX idx_pvr_version ON project_version_relations(version_id);
            """)
            conn.commit()
            print("Created project_version_relations table.")

        # Migration: create project_dependencies table (跨项目依赖声明)
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'project_dependencies'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE project_dependencies (
                    id              VARCHAR(100) PRIMARY KEY,
                    source_project  VARCHAR(100) NOT NULL,
                    source_branch   VARCHAR(100) NOT NULL,
                    target_project  VARCHAR(100) NOT NULL,
                    target_branch   VARCHAR(100) NOT NULL,
                    relation_type   VARCHAR(20) NOT NULL,
                    pinned_version  VARCHAR(100),
                    is_validated    BOOLEAN NOT NULL DEFAULT FALSE,
                    validation_error TEXT,
                    declared_by     VARCHAR(200),
                    declared_at     TIMESTAMPTZ DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(source_project, source_branch, target_project),
                    FOREIGN KEY (source_project) REFERENCES menus(id) ON DELETE CASCADE,
                    FOREIGN KEY (target_project) REFERENCES menus(id) ON DELETE CASCADE,
                    FOREIGN KEY (pinned_version) REFERENCES project_versions(id) ON DELETE SET NULL
                );
                CREATE INDEX idx_pd_source ON project_dependencies(source_project, source_branch);
                CREATE INDEX idx_pd_target ON project_dependencies(target_project, target_branch);
                CREATE INDEX idx_pd_relation_type ON project_dependencies(relation_type);
            """)
            conn.commit()
            print("Created project_dependencies table.")

        # Migration: create project_dependency_relations table (依赖涉及的关联关系)
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'project_dependency_relations'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE project_dependency_relations (
                    id              VARCHAR(100) PRIMARY KEY,
                    dependency_id   VARCHAR(100) NOT NULL,
                    source_collection VARCHAR(200) NOT NULL,
                    source_field    VARCHAR(200) NOT NULL,
                    target_collection VARCHAR(200) NOT NULL,
                    estimated_records INTEGER DEFAULT 0,
                    validation_status VARCHAR(20) DEFAULT 'unknown',
                    validation_detail TEXT,
                    validated_at    TIMESTAMPTZ,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    FOREIGN KEY (dependency_id) REFERENCES project_dependencies(id) ON DELETE CASCADE
                );
                CREATE INDEX idx_pdr_dependency ON project_dependency_relations(dependency_id);
                CREATE INDEX idx_pdr_source_coll ON project_dependency_relations(source_collection);
                CREATE INDEX idx_pdr_target_coll ON project_dependency_relations(target_collection);
            """)
            conn.commit()
            print("Created project_dependency_relations table.")

        # Migration: create project_dependency_events table (依赖变更事件)
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'project_dependency_events'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE project_dependency_events (
                    id              VARCHAR(100) PRIMARY KEY,
                    event_type      VARCHAR(50) NOT NULL,
                    source_project  VARCHAR(100),
                    source_branch   VARCHAR(100),
                    affected_dependencies VARCHAR[] DEFAULT '{}',
                    severity        VARCHAR(20) NOT NULL,
                    message         TEXT NOT NULL,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    resolved_at     TIMESTAMPTZ,
                    resolved_by     VARCHAR(200)
                );
                CREATE INDEX idx_pde_event_type ON project_dependency_events(event_type);
                CREATE INDEX idx_pde_source ON project_dependency_events(source_project, source_branch);
                CREATE INDEX idx_pde_severity ON project_dependency_events(severity);
                CREATE INDEX idx_pde_unresolved ON project_dependency_events(resolved_at) WHERE resolved_at IS NULL;
            """)
            conn.commit()
            print("Created project_dependency_events table.")

        # Migration: create webhook_settings table (Webhook 配置)
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'webhook_settings'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE webhook_settings (
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
                INSERT INTO webhook_settings (id) VALUES (1);
            """)
            conn.commit()
            print("Created webhook_settings table.")

        # Migration: create webhook_logs table (Webhook 调用日志)
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'webhook_logs'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE webhook_logs (
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
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX idx_wl_event_type ON webhook_logs(event_type);
                CREATE INDEX idx_wl_created_at ON webhook_logs(created_at DESC);
                CREATE INDEX idx_wl_success ON webhook_logs(success);
            """)
            conn.commit()
            print("Created webhook_logs table.")

        # Migration: create webhook_rules table (Webhook 规则配置)
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'webhook_rules'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE webhook_rules (
                    id              VARCHAR(100) PRIMARY KEY,
                    name            VARCHAR(200) NOT NULL,
                    description     TEXT,
                    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
                    source_collections JSONB DEFAULT '[]'::jsonb,
                    trigger_event   VARCHAR(50) NOT NULL,
                    trigger_condition JSONB DEFAULT '{}'::jsonb,
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
                CREATE INDEX idx_wr_event ON webhook_rules(trigger_event);
                CREATE INDEX idx_wr_enabled ON webhook_rules(enabled);
            """)
            conn.commit()
            print("Created webhook_rules table.")

        # Migration: convert source_collection to source_collections array
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'webhook_rules' AND column_name = 'source_collection'
        """)
        if cur.fetchone():
            cur.execute("""
                ALTER TABLE webhook_rules
                DROP COLUMN source_collection,
                ADD COLUMN source_collections JSONB DEFAULT '[]'::jsonb
            """)
            conn.commit()
            print("Converted source_collection to source_collections array.")

        # Migration: add rule_id and rule_name columns to webhook_logs
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'webhook_logs' AND column_name = 'rule_id'
        """)
        if not cur.fetchone():
            cur.execute("""
                ALTER TABLE webhook_logs
                ADD COLUMN rule_id VARCHAR(100),
                ADD COLUMN rule_name VARCHAR(200)
            """)
            conn.commit()
            print("Added rule_id and rule_name columns to webhook_logs.")

        # Migration: add trigger_timing and rollback_on_failure to webhook_rules
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'webhook_rules' AND column_name = 'trigger_timing'
        """)
        if not cur.fetchone():
            cur.execute("""
                ALTER TABLE webhook_rules
                ADD COLUMN trigger_timing VARCHAR(10) DEFAULT 'after' CHECK (trigger_timing IN ('before', 'after')),
                ADD COLUMN rollback_on_failure BOOLEAN DEFAULT FALSE
            """)
            conn.commit()
            print("Added trigger_timing and rollback_on_failure columns to webhook_rules.")

        # Migration: create merge_backups table (支持 merge 回滚)
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'merge_backups'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE merge_backups (
                    id              VARCHAR(100) PRIMARY KEY,
                    merge_id        VARCHAR(100) NOT NULL REFERENCES merge_records(id) ON DELETE CASCADE,
                    collection      VARCHAR(100) NOT NULL,
                    backup_type     VARCHAR(10) NOT NULL CHECK (backup_type IN ('created', 'updated', 'deleted')),
                    record_id       VARCHAR(100) NOT NULL,
                    old_data        JSONB,
                    new_data        JSONB,
                    old_relations   JSONB,
                    new_relations   JSONB,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX idx_mb_merge ON merge_backups(merge_id);
                CREATE INDEX idx_mb_collection ON merge_backups(collection, merge_id);
            """)
            conn.commit()
            print("Created merge_backups table for rollback support.")

        # Migration: create column_views table (自定义列视图)
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'column_views'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE TABLE column_views (
                    id              SERIAL PRIMARY KEY,
                    page_id         VARCHAR(100) NOT NULL REFERENCES page_configs(id) ON DELETE CASCADE,
                    name            VARCHAR(100) NOT NULL,
                    is_public       BOOLEAN DEFAULT false,
                    creator_id      VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
                    is_default      BOOLEAN DEFAULT false,
                    columns         JSONB NOT NULL DEFAULT '[]'::jsonb,
                    sort_config     JSONB DEFAULT '[]'::jsonb,
                    filter_config   JSONB DEFAULT '[]'::jsonb,
                    group_config    JSONB DEFAULT NULL,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX idx_column_views_page ON column_views(page_id);
                CREATE INDEX idx_column_views_creator ON column_views(creator_id);
                CREATE INDEX idx_column_views_public ON column_views(is_public) WHERE is_public = true;
                CREATE UNIQUE INDEX idx_one_default_per_page ON column_views(page_id) WHERE is_default = true;
            """)
            conn.commit()
            print("Created column_views table.")

        # Seed menus (insert only if not exists)
        menus_inserted = 0
        for m in MENUS:
            cur.execute("SELECT id FROM menus WHERE id = %s", (m["id"],))
            if not cur.fetchone():
                cur.execute(
                    'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                    (m["id"], m["name"], m.get("icon"), m.get("pageId"), m.get("parentId"), m.get("order", 0), m.get("path"), psycopg2.extras.Json(m.get("roles", ["admin", "developer", "guest"]))),
                )
                menus_inserted += 1
        if menus_inserted > 0:
            print(f"Inserted {menus_inserted} menus.")
        else:
            print("Menus already exist, skipping.")

        # Update menu_type for seeded menus (巡检管理示例数据)
        # menu-2 是一级菜单（workspace），menu-2-1/2-2/2-3 是二级菜单（project），menu-2-3-1/2-3-2 是三级菜单（data）
        cur.execute("UPDATE menus SET menu_type = 'workspace' WHERE id = 'menu-2'")
        cur.execute("UPDATE menus SET menu_type = 'project' WHERE id IN ('menu-2-1', 'menu-2-2', 'menu-2-3')")
        cur.execute("UPDATE menus SET menu_type = 'data' WHERE id IN ('menu-2-3-1', 'menu-2-3-2')")
        conn.commit()
        print("Updated menu_type for seeded menus.")

        # Seed page_configs (insert only if not exists)
        configs_inserted = 0
        for pc in PAGE_CONFIGS:
            cur.execute("SELECT id FROM page_configs WHERE id = %s", (pc["id"],))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO page_configs (id, name, description, api_endpoint, fields, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (pc["id"], pc["name"], pc.get("description"), pc.get("apiEndpoint"),
                     psycopg2.extras.Json(pc["fields"]),
                     pc.get("createdAt"), pc.get("updatedAt")),
                )
                configs_inserted += 1
        if configs_inserted > 0:
            print(f"Inserted {configs_inserted} page configs.")

        # Seed dynamic data (insert only if not exists)
        data_inserted = 0
        for collection, records in DYNAMIC_DATA.items():
            for r in records:
                rid = r["id"]
                cur.execute("SELECT id FROM dynamic_data WHERE id = %s", (rid,))
                if not cur.fetchone():
                    created_at = r.get("createdAt")
                    data = {k: v for k, v in r.items() if k not in ("id", "createdAt")}
                    cur.execute(
                        "INSERT INTO dynamic_data (id, collection, data, created_at) VALUES (%s,%s,%s,%s)",
                        (rid, collection, psycopg2.extras.Json(data), created_at),
                    )
                    data_inserted += 1
        if data_inserted > 0:
            print(f"Inserted {data_inserted} dynamic data records.")

        # Seed default admin user if users table is empty
        cur.execute("SELECT COUNT(*) FROM users")
        user_count = cur.fetchone()[0]
        if user_count == 0:
            initial_admin_password = os.getenv('INIT_ADMIN_PASSWORD', '').strip()
            initial_admin_username = os.getenv('INIT_ADMIN_USERNAME', 'admin').strip() or 'admin'
            if initial_admin_password:
                from werkzeug.security import generate_password_hash
                cur.execute(
                    "INSERT INTO users (id, username, password_hash, display_name, role) VALUES (%s, %s, %s, %s, %s)",
                    ('user-admin', initial_admin_username, generate_password_hash(initial_admin_password), '管理员', 'admin'),
                )
                print(f"Default admin user created ({initial_admin_username} / env INIT_ADMIN_PASSWORD)")
            else:
                print("Users table is empty. Skip default admin creation because INIT_ADMIN_PASSWORD is not set.")

        # Seed built-in roles (idempotent). admin = superuser; developer/guest = editable presets.
        cur.execute("""
            INSERT INTO roles (id, name, description, is_system, is_superuser, default_page_access) VALUES
              ('admin',     '管理员',   '系统超级管理员，拥有全部权限',   TRUE, TRUE,  'write'),
              ('developer', '开发人员', '可读写所有数据，无管理功能权限', TRUE, FALSE, 'write'),
              ('guest',     '访客',     '只读访问',                       TRUE, FALSE, 'read'),
              ('kefu-guest','智能客服访客','智能客服 bot 专用只读角色，可见数据页需显式授予', TRUE, FALSE, 'none')
            ON CONFLICT (id) DO NOTHING
        """)
        # admin.roles is seeded only to admin (superuser bypasses anyway, but keep an explicit row
        # so the catalog renders it as granted for the admin role).
        cur.execute("""
            INSERT INTO role_permissions (role_id, permission_key)
            VALUES ('admin', 'admin.roles')
            ON CONFLICT DO NOTHING
        """)
        conn.commit()
        print("Seeded built-in roles (admin/developer/guest).")

        conn.commit()
        print("Seed data inserted successfully.")
    finally:
        conn.close()

    # seed 演示客服（幂等；失败不影响 init）—— 依赖 kefu 表与 kefu-guest 角色已建
    try:
        from seed_kefu import seed_kefu_demo
        seed_kefu_demo()
    except Exception as e:
        print(f"[warn] seed 演示客服失败（非致命）：{e}")


if __name__ == "__main__":
    init_db()
