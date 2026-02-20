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
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dynamic_data_collection ON dynamic_data(collection);

CREATE TABLE IF NOT EXISTS data_relations (
    collection          VARCHAR(200) NOT NULL,
    record_id           VARCHAR(100) NOT NULL,
    field_name          VARCHAR(200) NOT NULL,
    related_collection  VARCHAR(200) NOT NULL,
    related_id          VARCHAR(100) NOT NULL,
    PRIMARY KEY (collection, record_id, field_name, related_id)
);

CREATE INDEX IF NOT EXISTS idx_data_relations_reverse
    ON data_relations(related_collection, related_id);

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
    note            TEXT
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

        # Check if data exists
        cur.execute("SELECT COUNT(*) FROM menus")
        count = cur.fetchone()[0]
        if count > 0:
            print("Data already exists, skipping menu/config seed.")
        else:
            # Seed menus
            for m in MENUS:
                cur.execute(
                    'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                    (m["id"], m["name"], m.get("icon"), m.get("pageId"), m.get("parentId"), m.get("order", 0), m.get("path"), psycopg2.extras.Json(m.get("roles", ["admin", "developer", "guest"]))),
                )
            print(f"Inserted {len(MENUS)} menus.")

            # Seed page_configs
            for pc in PAGE_CONFIGS:
                cur.execute(
                    "INSERT INTO page_configs (id, name, description, api_endpoint, fields, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (pc["id"], pc["name"], pc.get("description"), pc.get("apiEndpoint"),
                     psycopg2.extras.Json(pc["fields"]),
                     pc.get("createdAt"), pc.get("updatedAt")),
                )
            print(f"Inserted {len(PAGE_CONFIGS)} page configs.")

            # Seed dynamic data
            total = 0
            for collection, records in DYNAMIC_DATA.items():
                for r in records:
                    rid = r["id"]
                    created_at = r.get("createdAt")
                    data = {k: v for k, v in r.items() if k not in ("id", "createdAt")}
                    cur.execute(
                        "INSERT INTO dynamic_data (id, collection, data, created_at) VALUES (%s,%s,%s,%s)",
                        (rid, collection, psycopg2.extras.Json(data), created_at),
                    )
                    total += 1
            print(f"Inserted {total} dynamic data records.")

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
