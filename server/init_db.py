import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import psycopg2
import psycopg2.extras
from config import DB_CONFIG
from seed_data import MENUS, PAGE_CONFIGS, DYNAMIC_DATA
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
    id          VARCHAR(100) PRIMARY KEY,
    collection  VARCHAR(200) NOT NULL,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    version     INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_dynamic_data_collection ON dynamic_data(collection);
CREATE INDEX IF NOT EXISTS idx_dynamic_data_coll_branch ON dynamic_data(collection, branch_id);
CREATE INDEX IF NOT EXISTS idx_dynamic_data_coll_branch_created ON dynamic_data(collection, branch_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dynamic_data_gin ON dynamic_data USING gin(data);

CREATE TABLE IF NOT EXISTS data_relations (
    collection          VARCHAR(200) NOT NULL,
    record_id           VARCHAR(100) NOT NULL,
    field_name          VARCHAR(200) NOT NULL,
    related_collection  VARCHAR(200) NOT NULL,
    related_id          VARCHAR(100) NOT NULL
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
    role            VARCHAR(50)  NOT NULL DEFAULT 'guest'
                    CHECK (role IN ('admin', 'developer', 'guest')),
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
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO ai_settings (id) VALUES (1) ON CONFLICT DO NOTHING;

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
"""


def init_db():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()

        # Create tables
        cur.execute(DDL)
        conn.commit()
        print("Tables created.")

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
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );
                INSERT INTO ai_settings (id, enabled, api_key)
                VALUES (1, TRUE, 'sk-d234f87ccfce4893be2b17781a054546');
            """)
            conn.commit()
            print("Created ai_settings table.")

        # Migration: add AI settings menu if missing
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-11'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                "VALUES ('menu-3-11', %s, 'MagicStick', NULL, 'menu-3-a', 5, '/admin/ai-settings', %s)",
                ('AI 配置', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added AI settings menu.")

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
            cur.execute("ALTER TABLE dynamic_data DROP CONSTRAINT dynamic_data_pkey")
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
            from werkzeug.security import generate_password_hash
            cur.execute(
                "INSERT INTO users (id, username, password_hash, display_name, role) VALUES (%s, %s, %s, %s, %s)",
                ('user-admin', 'admin', generate_password_hash('admin123'), '管理员', 'admin'),
            )
            print("Default admin user created (admin / admin123)")

        conn.commit()
        print("Seed data inserted successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
