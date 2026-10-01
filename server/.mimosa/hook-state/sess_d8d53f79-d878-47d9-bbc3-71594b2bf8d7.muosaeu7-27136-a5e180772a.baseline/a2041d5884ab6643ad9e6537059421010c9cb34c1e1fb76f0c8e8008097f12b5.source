"""RBAC 角色权限。"""

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
