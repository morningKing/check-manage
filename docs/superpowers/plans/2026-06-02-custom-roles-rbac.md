# Custom Roles RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 3 hardcoded roles (admin/developer/guest) with a customizable role system where each role's permissions (menu visibility, per-data-page CRUD, per-admin-feature toggles) are configurable through an admin UI and enforced authoritatively on the server.

**Architecture:** A `roles` table plus `role_permissions` (admin-feature grants) and `role_page_permissions` (per-page CRUD) drive enforcement. The JWT carries only the role slug; the backend resolves the permission set per-request from an in-memory cache (invalidated on edit). A `@require_permission(key)` decorator replaces `@admin_required`; `routes/dynamic.py` checks per-page CRUD inline. The frontend fetches the resolved permission set at login and gates menus/buttons/pages off `can()` / `canPage()` helpers (UX only — server is authoritative). `admin` is a permanent superuser that bypasses all checks.

**Tech Stack:** Python Flask + psycopg2 + PyJWT (backend), pytest (backend tests), Vue 3 + TypeScript + Pinia + Element Plus (frontend), Vitest (frontend tests).

**Spec:** `docs/superpowers/specs/2026-06-02-custom-roles-rbac-design.md`

---

## File Structure

**Backend — created:**
- `server/utils/permissions.py` — permission catalog + resolution + cache (single source of truth)
- `server/routes/roles.py` — `roles_bp` REST API for role/permission CRUD
- `server/tests/test_permissions.py` — unit tests for resolution/cache
- `server/tests/test_routes_roles.py` — route tests for role API
- `server/tests/test_dynamic_page_permissions.py` — per-page CRUD gating tests

**Backend — modified:**
- `server/auth.py` — add `require_permission` decorator
- `server/init_db.py` — DDL + migration for 3 tables, drop `users.role` CHECK, seed roles
- `server/routes/auth.py` — add `permissions` to login + `/auth/me` payload
- `server/routes/users.py` — validate role against `roles` table instead of hardcoded tuple
- `server/routes/dynamic.py` — per-page CRUD checks in CRUD handlers
- `server/app.py` — register `roles_bp` before `dynamic_bp`
- 23 route files — migrate `@admin_required` → `@require_permission('admin.<feature>')`

**Frontend — created:**
- `src/types/role.ts` — Role, PermissionCatalog, ResolvedPermissions types
- `src/api/role.ts` — role API client
- `src/stores/role.ts` — role management store
- `src/views/admin/RoleManager.vue` — role management page
- `src/stores/__tests__/permissions.test.ts` — auth-store `can`/`canPage` tests

**Frontend — modified:**
- `src/types/user.ts` — `UserRole` becomes `string`; `UserInfo` gains `permissions`
- `src/stores/auth.ts` — store resolved permissions; add `can()` / `canPage()`; rework `hasRoutePermission`
- `src/router/index.ts` — register `/admin/roles` route
- `src/components/layout/SideMenu.vue` — gate admin links by `can()`
- `src/views/admin/UserManager.vue` — fetch roles for the dropdown
- `src/views/dynamic/DynamicPage.vue` (+ `DataTable.vue` / `DynamicForm.vue` usage) — gate New/Edit/Delete by `canPage()`

---

## Reference: Admin route → capability key map

Used in Phase 3 (decorator migration) and Phase 5 (router/menu gating). Each `/admin/*` path and its backing route file map to exactly one capability key.

| Capability key | Route file(s) | `/admin` path |
|----------------|---------------|---------------|
| `admin.users` | `routes/users.py` | `/admin/users` |
| `admin.menus` | `routes/menus.py`, `routes/menu_export.py` | `/admin/menu`, `/admin/menu-export` |
| `admin.page_configs` | `routes/page_configs.py` | `/admin/page-config` |
| `admin.backup` | `routes/backups.py` | `/admin/backup`, `/admin/factory-reset` |
| `admin.export_scripts` | `routes/export_scripts.py` | `/admin/export-scripts` |
| `admin.api_keys` | `routes/api_keys.py` | `/admin/api-keys` |
| `admin.validation_scripts` | `routes/validation_scripts.py` | `/admin/validation-scripts` |
| `admin.etl_tasks` | `routes/etl_tasks.py` | `/admin/etl-tasks` |
| `admin.query` | `routes/query.py` | `/admin/query` |
| `admin.trigger_rules` | `routes/trigger_rules.py` | `/admin/trigger-rules` |
| `admin.ai_settings` | `routes/ai.py` | `/admin/ai-settings` |
| `admin.webhooks` | `routes/webhooks.py` | `/admin/webhook-settings` |
| `admin.dependencies` | `routes/cross_project_dependencies.py` | `/admin/dependency-manager` |
| `admin.system_config` | `routes/system_config.py` | `/admin/system-settings` |
| `admin.operation_logs` | `routes/operation_logs.py` | `/admin/operation-log` |
| `admin.project_versions` | `routes/project_versions.py` | (in-page) |
| `admin.dashboards` | `routes/dashboards.py` | `/dashboard` (write ops) |
| `admin.home_widgets` | `routes/home_widgets.py` | (home admin) |
| `admin.column_views` | `routes/column_views.py` | (in-page) |
| `admin.comments` | `routes/comments.py` | (in-page) |
| `admin.roles` | `routes/roles.py` | `/admin/roles` |

Note: `factory-reset` is grouped under `admin.backup`; `menu-export` under `admin.menus`. `routes/relations.py`'s `@write_required` ops are data-adjacent — see Phase 3 Task 3.6.

---

## Phase 0 — Database schema & seeding

### Task 0.1: Add DDL for the three RBAC tables

**Files:**
- Modify: `server/init_db.py` (add a new DDL constant near `DATA_FILES_DDL`, ~line 406)

- [ ] **Step 1: Add the RBAC DDL constant**

After the `AI_CHAT_SESSIONS_BATCH_COLUMNS_DDL` block (~line 467), add:

```python
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
```

- [ ] **Step 2: Execute the DDL in `init_db()`**

In `init_db()`, after the `AI_CHAT_SESSIONS_BATCH_COLUMNS_DDL` execution block (~line 496), add:

```python
        cur.execute(RBAC_DDL)
        conn.commit()
        print("RBAC tables (roles, role_permissions, role_page_permissions) created.")
```

- [ ] **Step 3: Run init_db to verify tables create cleanly**

Run: `cd server && python init_db.py`
Expected: output includes `RBAC tables (roles, role_permissions, role_page_permissions) created.` and no error.

- [ ] **Step 4: Verify tables exist**

Run: `cd server && python -c "import psycopg2; from config import DB_CONFIG; c=psycopg2.connect(**DB_CONFIG); cur=c.cursor(); cur.execute(\"SELECT table_name FROM information_schema.tables WHERE table_name IN ('roles','role_permissions','role_page_permissions') ORDER BY table_name\"); print([r[0] for r in cur.fetchall()])"`
Expected: `['role_page_permissions', 'role_permissions', 'roles']`

- [ ] **Step 5: Commit**

```bash
git add server/init_db.py
git commit -m "feat(rbac): add roles / role_permissions / role_page_permissions tables"
```

---

### Task 0.2: Drop the `users.role` CHECK constraint (migration)

**Files:**
- Modify: `server/init_db.py` (add migration block inside `init_db()`)

- [ ] **Step 1: Add idempotent migration to drop the CHECK**

In `init_db()`, after the RBAC DDL execution from Task 0.1, add:

```python
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
```

- [ ] **Step 2: Also relax the inline DDL definition**

In the `DDL` string (~line 84), change the `users.role` column definition from:

```python
    role            VARCHAR(50)  NOT NULL DEFAULT 'guest'
                    CHECK (role IN ('admin', 'developer', 'guest')),
```

to:

```python
    role            VARCHAR(50)  NOT NULL DEFAULT 'guest',
```

(The constraint is now enforced at the application layer against the `roles` table.)

- [ ] **Step 3: Run init_db and verify constraint is gone**

Run: `cd server && python init_db.py`
Then: `cd server && python -c "import psycopg2; from config import DB_CONFIG; c=psycopg2.connect(**DB_CONFIG); cur=c.cursor(); cur.execute(\"SELECT count(*) FROM pg_constraint WHERE conrelid='users'::regclass AND contype='c' AND pg_get_constraintdef(oid) ILIKE '%role%'\"); print('role checks:', cur.fetchone()[0])"`
Expected: `role checks: 0`

- [ ] **Step 4: Commit**

```bash
git add server/init_db.py
git commit -m "feat(rbac): drop hardcoded users.role CHECK, validate at app layer"
```

---

### Task 0.3: Seed the three built-in roles

**Files:**
- Modify: `server/init_db.py` (add seeding block, after the default admin user seed ~line 1737)

- [ ] **Step 1: Add idempotent role seeding**

In `init_db()`, after the default-admin-user seeding block (~line 1740), add:

```python
        # Seed built-in roles (idempotent). admin = superuser; developer/guest = editable presets.
        cur.execute("""
            INSERT INTO roles (id, name, description, is_system, is_superuser, default_page_access) VALUES
              ('admin',     '管理员',   '系统超级管理员，拥有全部权限',   TRUE, TRUE,  'write'),
              ('developer', '开发人员', '可读写所有数据，无管理功能权限', TRUE, FALSE, 'write'),
              ('guest',     '访客',     '只读访问',                       TRUE, FALSE, 'read')
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
```

- [ ] **Step 2: Run init_db and verify seed**

Run: `cd server && python init_db.py`
Then: `cd server && python -c "import psycopg2; from config import DB_CONFIG; c=psycopg2.connect(**DB_CONFIG); cur=c.cursor(); cur.execute('SELECT id, is_superuser, default_page_access FROM roles ORDER BY id'); print(cur.fetchall())"`
Expected: `[('admin', True, 'write'), ('developer', False, 'write'), ('guest', False, 'read')]`

- [ ] **Step 3: Commit**

```bash
git add server/init_db.py
git commit -m "feat(rbac): seed built-in admin/developer/guest roles"
```

---

## Phase 1 — Permission resolution module

### Task 1.1: Permission catalog constant

**Files:**
- Create: `server/utils/permissions.py`
- Test: `server/tests/test_permissions.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_permissions.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.permissions import PERMISSION_CATALOG, catalog_keys


def test_catalog_has_expected_keys():
    keys = catalog_keys()
    assert 'admin.users' in keys
    assert 'admin.roles' in keys
    assert 'admin.backup' in keys
    # every entry has key + label + group
    for entry in PERMISSION_CATALOG:
        assert entry['key'] and entry['label'] and entry['group']


def test_catalog_keys_are_unique():
    keys = catalog_keys()
    assert len(keys) == len(set(keys))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_permissions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.permissions'`

- [ ] **Step 3: Create the catalog**

Create `server/utils/permissions.py`:

```python
"""Permission catalog + role permission resolution with in-memory cache.

Single source of truth for RBAC. The JWT only carries the role slug; every
request resolves the permission set from this module (cached), so editing a
role takes effect immediately without re-login.
"""
import threading
from db import get_db

# Admin-feature capability catalog. Rendered as toggles in the role manager.
PERMISSION_CATALOG = [
    {'key': 'admin.users',              'label': '用户管理',   'group': '平台管理'},
    {'key': 'admin.roles',              'label': '角色权限',   'group': '平台管理'},
    {'key': 'admin.menus',              'label': '菜单管理',   'group': '平台管理'},
    {'key': 'admin.page_configs',       'label': '页面配置',   'group': '平台管理'},
    {'key': 'admin.api_keys',           'label': 'Open API',  'group': '平台管理'},
    {'key': 'admin.ai_settings',        'label': 'AI 配置',   'group': '平台管理'},
    {'key': 'admin.export_scripts',     'label': '导出脚本',   'group': '数据工具'},
    {'key': 'admin.validation_scripts', 'label': '校验脚本',   'group': '数据工具'},
    {'key': 'admin.etl_tasks',          'label': 'ETL 管理',  'group': '数据工具'},
    {'key': 'admin.query',              'label': '数据查询',   'group': '数据工具'},
    {'key': 'admin.trigger_rules',      'label': '触发规则',   'group': '数据工具'},
    {'key': 'admin.webhooks',           'label': 'Webhook',   'group': '数据工具'},
    {'key': 'admin.dependencies',       'label': '依赖管理',   'group': '数据工具'},
    {'key': 'admin.dashboards',         'label': '仪表盘管理', 'group': '数据工具'},
    {'key': 'admin.column_views',       'label': '列视图管理', 'group': '数据工具'},
    {'key': 'admin.comments',           'label': '评论管理',   'group': '数据工具'},
    {'key': 'admin.project_versions',   'label': '版本管理',   'group': '数据工具'},
    {'key': 'admin.operation_logs',     'label': '操作日志',   'group': '系统运维'},
    {'key': 'admin.backup',             'label': '系统备份',   'group': '系统运维'},
    {'key': 'admin.system_config',      'label': '系统设置',   'group': '系统运维'},
    {'key': 'admin.home_widgets',       'label': '首页区块',   'group': '系统运维'},
]


def catalog_keys():
    return [e['key'] for e in PERMISSION_CATALOG]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_permissions.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add server/utils/permissions.py server/tests/test_permissions.py
git commit -m "feat(rbac): permission catalog"
```

---

### Task 1.2: Role permission resolution + cache

**Files:**
- Modify: `server/utils/permissions.py`
- Test: `server/tests/test_permissions.py`

- [ ] **Step 1: Write failing tests for resolution**

Append to `server/tests/test_permissions.py`:

```python
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
import utils.permissions as perms


def _mock_db(role_row, admin_rows, page_rows):
    """role_row: tuple or None; admin_rows: list[(key,)]; page_rows: list[(page_id,r,c,u,d)]"""
    cur = MagicMock()
    # fetchone for the roles row; fetchall called twice (admin keys, then page perms)
    cur.fetchone.return_value = role_row
    cur.fetchall.side_effect = [admin_rows, page_rows]
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda self, *a: None

    @contextmanager
    def fake_get_db():
        yield conn
    return fake_get_db


def setup_function():
    perms.invalidate_cache()


def test_superuser_bypasses_everything():
    fake = _mock_db(('admin', True, 'write'), [], [])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_admin('admin', 'admin.anything') is True
        assert perms.can_page('admin', 'page-orders', 'delete') is True


def test_admin_key_grant():
    fake = _mock_db(('developer', False, 'write'), [('admin.etl_tasks',)], [])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_admin('developer', 'admin.etl_tasks') is True
        assert perms.can_admin('developer', 'admin.users') is False


def test_page_default_fallback_read():
    fake = _mock_db(('guest', False, 'read'), [], [])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_page('guest', 'page-orders', 'read') is True
        assert perms.can_page('guest', 'page-orders', 'update') is False


def test_page_default_fallback_write():
    fake = _mock_db(('developer', False, 'write'), [], [])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_page('developer', 'page-orders', 'create') is True
        assert perms.can_page('developer', 'page-orders', 'delete') is True


def test_page_explicit_row_overrides_default():
    fake = _mock_db(('developer', False, 'write'), [],
                    [('page-orders', True, False, True, False)])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_page('developer', 'page-orders', 'update') is True
        assert perms.can_page('developer', 'page-orders', 'create') is False
        # other pages still use default
        assert perms.can_page('developer', 'page-other', 'create') is True


def test_unknown_role_denies():
    fake = _mock_db(None, [], [])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_admin('ghost', 'admin.users') is False
        assert perms.can_page('ghost', 'page-orders', 'read') is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_permissions.py -v`
Expected: FAIL with `AttributeError: module 'utils.permissions' has no attribute 'invalidate_cache'`

- [ ] **Step 3: Implement resolution + cache**

Append to `server/utils/permissions.py`:

```python
_cache = {}            # role_id -> resolved dict
_lock = threading.Lock()

_ACTION_COLUMN = {'read': 'read', 'create': 'create', 'update': 'update', 'delete': 'delete'}


def _load(role_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, is_superuser, default_page_access FROM roles WHERE id = %s',
            (role_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute(
            'SELECT permission_key FROM role_permissions WHERE role_id = %s',
            (role_id,),
        )
        admin_keys = {r[0] for r in cur.fetchall()}
        cur.execute(
            'SELECT page_id, can_read, can_create, can_update, can_delete '
            'FROM role_page_permissions WHERE role_id = %s',
            (role_id,),
        )
        page_perms = {
            r[0]: {'read': r[1], 'create': r[2], 'update': r[3], 'delete': r[4]}
            for r in cur.fetchall()
        }
    return {
        'is_superuser': bool(row[1]),
        'default_page_access': row[2],
        'admin_keys': admin_keys,
        'page_perms': page_perms,
    }


def get_role_perms(role_id):
    """Return the resolved permission dict for a role (cached), or None if unknown."""
    if role_id in _cache:
        return _cache[role_id]
    with _lock:
        if role_id in _cache:
            return _cache[role_id]
        resolved = _load(role_id)
        if resolved is not None:
            _cache[role_id] = resolved
        return resolved


def invalidate_cache(role_id=None):
    """Clear cache for one role, or all roles when role_id is None."""
    with _lock:
        if role_id is None:
            _cache.clear()
        else:
            _cache.pop(role_id, None)


def can_admin(role_id, key):
    p = get_role_perms(role_id)
    if not p:
        return False
    return p['is_superuser'] or key in p['admin_keys']


def _default_allows(default_page_access, action):
    if default_page_access == 'none':
        return False
    if default_page_access == 'read':
        return action == 'read'
    # 'write'
    return True


def can_page(role_id, page_id, action):
    p = get_role_perms(role_id)
    if not p:
        return False
    if p['is_superuser']:
        return True
    row = p['page_perms'].get(page_id)
    if row is not None:
        return bool(row.get(action, False))
    return _default_allows(p['default_page_access'], action)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_permissions.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Add a cache-invalidation test**

Append to `server/tests/test_permissions.py`:

```python
def test_cache_invalidation_reloads():
    fake1 = _mock_db(('developer', False, 'read'), [], [])
    with patch('utils.permissions.get_db', fake1):
        assert perms.can_page('developer', 'page-x', 'create') is False
    # change underlying data to 'write', but cache still holds old value
    fake2 = _mock_db(('developer', False, 'write'), [], [])
    with patch('utils.permissions.get_db', fake2):
        assert perms.can_page('developer', 'page-x', 'create') is False  # cached
        perms.invalidate_cache('developer')
        assert perms.can_page('developer', 'page-x', 'create') is True   # reloaded
```

- [ ] **Step 6: Run and commit**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_permissions.py -v`
Expected: PASS

```bash
git add server/utils/permissions.py server/tests/test_permissions.py
git commit -m "feat(rbac): role permission resolution with in-memory cache"
```

---

### Task 1.3: `require_permission` decorator

**Files:**
- Modify: `server/auth.py`
- Test: `server/tests/test_permissions.py`

- [ ] **Step 1: Write the failing test**

Append to `server/tests/test_permissions.py`:

```python
from flask import Flask, jsonify
from auth import require_permission, create_token


def _app_with_protected_route():
    app = Flask(__name__)

    @app.route('/protected')
    @require_permission('admin.users')
    def protected():
        return jsonify({'ok': True})

    app.config['TESTING'] = True
    return app


def test_require_permission_allows_granted():
    app = _app_with_protected_route()
    token = create_token({'id': 'u1', 'username': 'dev', 'role': 'developer'})
    fake = _mock_db(('developer', False, 'read'), [('admin.users',)], [])
    with patch('utils.permissions.get_db', fake):
        perms.invalidate_cache()
        resp = app.test_client().get('/protected', headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 200


def test_require_permission_denies_ungranted():
    app = _app_with_protected_route()
    token = create_token({'id': 'u1', 'username': 'guest', 'role': 'guest'})
    fake = _mock_db(('guest', False, 'read'), [], [])
    with patch('utils.permissions.get_db', fake):
        perms.invalidate_cache()
        resp = app.test_client().get('/protected', headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 403


def test_require_permission_requires_login():
    app = _app_with_protected_route()
    resp = app.test_client().get('/protected')
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_permissions.py -k require_permission -v`
Expected: FAIL with `ImportError: cannot import name 'require_permission' from 'auth'`

- [ ] **Step 3: Implement the decorator**

In `server/auth.py`, add after the `admin_required` function (~line 100):

```python
def require_permission(permission_key):
    """Decorator: require the current user's role to hold `permission_key`
    (superuser bypasses). Implies login_required.
    """
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                return jsonify({'error': '未登录'}), 401
            token = auth_header.split(' ', 1)[1]
            payload = decode_token(token)
            if not payload:
                return jsonify({'error': '登录已过期'}), 401
            from utils.permissions import can_admin
            if not can_admin(payload.get('role'), permission_key):
                return jsonify({'error': '权限不足'}), 403
            g.current_user = payload
            return f(*args, **kwargs)
        return decorated
    return wrapper
```

(Import is done inside the function to avoid a circular import: `utils.permissions` imports `db`, not `auth`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_permissions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/auth.py server/tests/test_permissions.py
git commit -m "feat(rbac): require_permission decorator"
```

---

## Phase 2 — Roles REST API

### Task 2.1: roles_bp with list/catalog/CRUD

**Files:**
- Create: `server/routes/roles.py`
- Modify: `server/app.py`
- Test: `server/tests/test_routes_roles.py`

- [ ] **Step 1: Write failing route tests**

Create `server/tests/test_routes_roles.py`:

```python
import sys, os, json
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


@pytest.fixture
def setup(mock_conn, mock_cursor):
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('routes.roles.get_db', fake_db),
        patch('utils.permissions.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
    ]
    for p in patches:
        p.start()
    import utils.permissions as perms
    perms.invalidate_cache()
    # superuser admin so require_permission('admin.roles') passes
    mock_cursor.fetchone.return_value = ('admin', True, 'write')
    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    yield app.test_client(), mock_cursor, {'Authorization': f'Bearer {admin}'}
    for p in patches:
        p.stop()


def test_get_catalog(setup):
    client, _, headers = setup
    resp = client.get('/roles/catalog', headers=headers)
    assert resp.status_code == 200
    keys = [e['key'] for e in resp.get_json()]
    assert 'admin.users' in keys


def test_list_roles(setup):
    client, cur, headers = setup
    # superuser check uses fetchone; list query uses fetchall
    cur.fetchall.return_value = [
        ('admin', '管理员', '', True, True, 'write'),
        ('guest', '访客', '', True, False, 'read'),
    ]
    resp = client.get('/roles', headers=headers)
    assert resp.status_code == 200
    assert len(resp.get_json()) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_roles.py -v`
Expected: FAIL (404 / import error — `routes.roles` not registered)

- [ ] **Step 3: Implement roles_bp**

Create `server/routes/roles.py`:

```python
from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import require_permission
from utils.permissions import PERMISSION_CATALOG, catalog_keys, invalidate_cache
from utils.operation_log import log_operation
import uuid

roles_bp = Blueprint('roles', __name__)


def _role_to_dict(row):
    return {
        'id': row[0], 'name': row[1], 'description': row[2] or '',
        'isSystem': row[3], 'isSuperuser': row[4], 'defaultPageAccess': row[5],
    }


@roles_bp.route('/roles/catalog', methods=['GET'])
@require_permission('admin.roles')
def get_catalog():
    return jsonify(PERMISSION_CATALOG)


@roles_bp.route('/roles', methods=['GET'])
@require_permission('admin.roles')
def list_roles():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, description, is_system, is_superuser, '
                    'default_page_access FROM roles ORDER BY is_system DESC, created_at')
        rows = cur.fetchall()
    return jsonify([_role_to_dict(r) for r in rows])


@roles_bp.route('/roles/<role_id>', methods=['GET'])
@require_permission('admin.roles')
def get_role(role_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, description, is_system, is_superuser, '
                    'default_page_access FROM roles WHERE id = %s', (role_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '角色不存在'}), 404
        cur.execute('SELECT permission_key FROM role_permissions WHERE role_id = %s', (role_id,))
        admin_keys = [r[0] for r in cur.fetchall()]
        cur.execute('SELECT page_id, can_read, can_create, can_update, can_delete '
                    'FROM role_page_permissions WHERE role_id = %s', (role_id,))
        pages = [{'pageId': r[0], 'canRead': r[1], 'canCreate': r[2],
                  'canUpdate': r[3], 'canDelete': r[4]} for r in cur.fetchall()]
    data = _role_to_dict(row)
    data['adminKeys'] = admin_keys
    data['pagePermissions'] = pages
    return jsonify(data)


@roles_bp.route('/roles', methods=['POST'])
@require_permission('admin.roles')
def create_role():
    body = request.get_json(force=True)
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': '角色名称不能为空'}), 400
    default_access = body.get('defaultPageAccess', 'read')
    if default_access not in ('none', 'read', 'write'):
        return jsonify({'error': '无效的默认数据页权限'}), 400
    role_id = f'role-{uuid.uuid4().hex[:8]}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO roles (id, name, description, is_system, is_superuser, default_page_access) '
            'VALUES (%s,%s,%s,FALSE,FALSE,%s)',
            (role_id, name, body.get('description', ''), default_access),
        )
    log_operation('create', 'role', role_id, name, f'新增角色「{name}」')
    invalidate_cache(role_id)
    return jsonify({'id': role_id, 'name': name}), 201


@roles_bp.route('/roles/<role_id>', methods=['PUT'])
@require_permission('admin.roles')
def update_role(role_id):
    body = request.get_json(force=True)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT is_superuser, is_system FROM roles WHERE id = %s', (role_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '角色不存在'}), 404
        is_superuser = row[0]

        # Update scalar fields
        sets, params = [], []
        if 'name' in body and body['name'].strip():
            sets.append('name = %s'); params.append(body['name'].strip())
        if 'description' in body:
            sets.append('description = %s'); params.append(body['description'])
        if 'defaultPageAccess' in body:
            if body['defaultPageAccess'] not in ('none', 'read', 'write'):
                return jsonify({'error': '无效的默认数据页权限'}), 400
            sets.append('default_page_access = %s'); params.append(body['defaultPageAccess'])
        if sets:
            sets.append('updated_at = NOW()')
            params.append(role_id)
            cur.execute(f'UPDATE roles SET {", ".join(sets)} WHERE id = %s', params)

        # Replace admin permission keys (superuser keeps all — ignore inbound changes)
        if 'adminKeys' in body and not is_superuser:
            valid = set(catalog_keys())
            keys = [k for k in body['adminKeys'] if k in valid]
            cur.execute('DELETE FROM role_permissions WHERE role_id = %s', (role_id,))
            for k in keys:
                cur.execute('INSERT INTO role_permissions (role_id, permission_key) '
                            'VALUES (%s,%s) ON CONFLICT DO NOTHING', (role_id, k))

        # Replace page permissions (superuser bypasses anyway)
        if 'pagePermissions' in body and not is_superuser:
            cur.execute('DELETE FROM role_page_permissions WHERE role_id = %s', (role_id,))
            for pp in body['pagePermissions']:
                cur.execute(
                    'INSERT INTO role_page_permissions '
                    '(role_id, page_id, can_read, can_create, can_update, can_delete) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (role_id, pp['pageId'], bool(pp.get('canRead', True)),
                     bool(pp.get('canCreate', False)), bool(pp.get('canUpdate', False)),
                     bool(pp.get('canDelete', False))),
                )
    log_operation('update', 'role', role_id, body.get('name', role_id), f'更新角色权限「{role_id}」')
    invalidate_cache(role_id)
    return jsonify({'message': '更新成功'})


@roles_bp.route('/roles/<role_id>', methods=['DELETE'])
@require_permission('admin.roles')
def delete_role(role_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name, is_system FROM roles WHERE id = %s', (role_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '角色不存在'}), 404
        if row[1]:
            return jsonify({'error': '内置角色不可删除'}), 400
        cur.execute('SELECT username FROM users WHERE role = %s', (role_id,))
        users = [r[0] for r in cur.fetchall()]
        if users:
            return jsonify({'error': f'该角色仍被用户使用：{", ".join(users)}，请先重新分配'}), 409
        # Scrub the slug from all menus.roles arrays (transactional with the delete)
        cur.execute("UPDATE menus SET roles = roles - %s WHERE roles ? %s", (role_id, role_id))
        cur.execute('DELETE FROM roles WHERE id = %s', (role_id,))
    log_operation('delete', 'role', role_id, row[0], f'删除角色「{row[0]}」')
    invalidate_cache(role_id)
    return jsonify({})
```

- [ ] **Step 4: Register the blueprint**

In `server/app.py`, add the import after line 36 (`from routes.ai_chat import ai_chat_bp`):

```python
from routes.roles import roles_bp
```

And register it before `dynamic_bp` (after line 78, before line 79 `app.register_blueprint(dynamic_bp)`):

```python
app.register_blueprint(roles_bp)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_roles.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add server/routes/roles.py server/app.py server/tests/test_routes_roles.py
git commit -m "feat(rbac): roles REST API (catalog, CRUD, page-permissions, menu-slug scrub)"
```

---

### Task 2.2: Delete guards — superuser undeletable, in-use blocked

**Files:**
- Test: `server/tests/test_routes_roles.py`

- [ ] **Step 1: Write failing tests**

Append to `server/tests/test_routes_roles.py`:

```python
def test_delete_system_role_blocked(setup):
    client, cur, headers = setup
    # superuser check (fetchone #1) then role lookup (fetchone #2)
    cur.fetchone.side_effect = [('admin', True, 'write'), ('管理员', True)]
    resp = client.delete('/roles/admin', headers=headers)
    assert resp.status_code == 400


def test_delete_role_in_use_blocked(setup):
    client, cur, headers = setup
    cur.fetchone.side_effect = [('admin', True, 'write'), ('质检员', False)]
    cur.fetchall.return_value = [('zhang',), ('li',)]
    resp = client.delete('/roles/role-abc', headers=headers)
    assert resp.status_code == 409
```

- [ ] **Step 2: Run tests**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_roles.py -k delete -v`
Expected: PASS (guards already implemented in Task 2.1 — these lock in the behavior)

- [ ] **Step 3: Commit**

```bash
git add server/tests/test_routes_roles.py
git commit -m "test(rbac): role delete guards (system role, in-use)"
```

---

## Phase 3 — Backend enforcement migration

### Task 3.1: Add `permissions` to auth payload

**Files:**
- Modify: `server/routes/auth.py`
- Test: `server/tests/test_routes_auth.py`

- [ ] **Step 1: Write the failing test**

Append a test class to `server/tests/test_routes_auth.py` (after existing classes):

```python
class TestAuthPermissionsPayload:
    def test_me_includes_permissions(self, setup):
        client, cur, headers = setup
        # /auth/me fetchone: user row, then permissions resolution fetchone: role row
        cur.fetchone.side_effect = [
            ('user-admin', 'admin', '管理员', 'admin'),   # user row
            ('admin', True, 'write'),                      # role row (resolution)
        ]
        cur.fetchall.side_effect = [[], []]                # admin_keys, page_perms
        resp = client.get('/auth/me', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'permissions' in data
        assert data['permissions']['isSuperuser'] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_auth.py -k permissions -v`
Expected: FAIL with `KeyError: 'permissions'` / assertion error

- [ ] **Step 3: Implement the payload helper and wire it in**

In `server/routes/auth.py`, add a helper near the top (after imports):

```python
def build_permissions_payload(role_id):
    """Resolve a compact permission set for the frontend."""
    from utils.permissions import get_role_perms
    p = get_role_perms(role_id)
    if not p:
        return {'isSuperuser': False, 'adminKeys': [],
                'defaultPageAccess': 'none', 'pagePerms': {}}
    return {
        'isSuperuser': p['is_superuser'],
        'adminKeys': sorted(p['admin_keys']),
        'defaultPageAccess': p['default_page_access'],
        'pagePerms': p['page_perms'],
    }
```

In `login()`, change the returned `user`/response to include permissions:

```python
    user = {
        'id': row[0],
        'username': row[1],
        'displayName': row[3],
        'role': row[4],
    }
    token = create_token(user)
    user['permissions'] = build_permissions_payload(row[4])
    return jsonify({'token': token, 'user': user})
```

In `get_current_user()`, change the final return to:

```python
    return jsonify({
        'id': row[0],
        'username': row[1],
        'displayName': row[2],
        'role': row[3],
        'permissions': build_permissions_payload(row[3]),
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/auth.py server/tests/test_routes_auth.py
git commit -m "feat(rbac): include resolved permissions in login/me payload"
```

---

### Task 3.2: Validate user role against roles table

**Files:**
- Modify: `server/routes/users.py`
- Test: `server/tests/test_routes_users.py` (create if missing — check first)

- [ ] **Step 1: Add a role-existence helper**

In `server/routes/users.py`, add after imports:

```python
def _role_exists(cur, role_id):
    cur.execute('SELECT 1 FROM roles WHERE id = %s', (role_id,))
    return cur.fetchone() is not None
```

- [ ] **Step 2: Replace the hardcoded checks in `create_user`**

Change (line ~51):

```python
    if role not in ('admin', 'developer', 'guest'):
        return jsonify({'error': '无效的角色'}), 400
```

to (move it inside the `with get_db()` block, after acquiring `cur`, before the duplicate-username check):

```python
        if not _role_exists(cur, role):
            return jsonify({'error': '无效的角色'}), 400
```

(Remove the old top-level check.)

- [ ] **Step 3: Replace the hardcoded check in `update_user`**

Change (line ~85):

```python
        if 'role' in body:
            if body['role'] not in ('admin', 'developer', 'guest'):
                return jsonify({'error': '无效的角色'}), 400
            sets.append('role = %s')
            params.append(body['role'])
```

to:

```python
        if 'role' in body:
            if not _role_exists(cur, body['role']):
                return jsonify({'error': '无效的角色'}), 400
            sets.append('role = %s')
            params.append(body['role'])
```

- [ ] **Step 4: Run existing user route tests**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/ -k user -v`
Expected: PASS (mock cursor returns a row for `_role_exists` where needed; adjust any failing test's `fetchone` mock to include a truthy role row before the insert).

- [ ] **Step 5: Commit**

```bash
git add server/routes/users.py
git commit -m "feat(rbac): validate user role against roles table"
```

---

### Task 3.3: Per-page CRUD gating in dynamic.py — write ops

**Files:**
- Modify: `server/routes/dynamic.py`
- Test: `server/tests/test_dynamic_page_permissions.py`

- [ ] **Step 1: Write failing tests**

Create `server/tests/test_dynamic_page_permissions.py`:

```python
import sys, os, json
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


@pytest.fixture
def setup(mock_conn, mock_cursor):
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('routes.dynamic.get_db', fake_db),
        patch('utils.permissions.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
    ]
    for p in patches:
        p.start()
    import utils.permissions as perms
    perms.invalidate_cache()
    from app import app
    app.config['TESTING'] = True
    yield app.test_client(), mock_cursor, perms
    for p in patches:
        p.stop()


def _token(role):
    return {'Authorization': f'Bearer {create_token({"id": "u", "username": "u", "role": role})}'}


def test_guest_create_forbidden(setup):
    client, cur, perms = setup
    cur.fetchone.return_value = ('guest', False, 'read')  # role row for resolution
    cur.fetchall.side_effect = [[], []]                   # admin_keys, page_perms
    resp = client.post('/orders', data=json.dumps({'name': 'x'}),
                       content_type='application/json', headers=_token('guest'))
    assert resp.status_code == 403


def test_developer_create_allowed_passes_gate(setup):
    client, cur, perms = setup
    # role resolution: developer/write; then create_item proceeds (will 4xx later for
    # other reasons, but NOT 403 from the permission gate)
    cur.fetchone.return_value = ('developer', False, 'write')
    cur.fetchall.side_effect = [[], []]
    resp = client.post('/orders', data=json.dumps({}),
                       content_type='application/json', headers=_token('developer'))
    assert resp.status_code != 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_dynamic_page_permissions.py -v`
Expected: FAIL — `test_guest_create_forbidden` gets non-403 (currently `write_required` blocks guest with 403 actually — but a *custom read-only role* would not. The test asserts the new per-page gate.)

- [ ] **Step 3: Add a gate helper and apply to write handlers**

In `server/routes/dynamic.py`, add after the imports / `RESERVED` set:

```python
from auth import login_required, write_required, decode_token
from utils.permissions import can_page


def _require_page_action(collection, action):
    """Return an error response tuple if the current user lacks `action`
    permission on this collection's page, else None."""
    user = getattr(flask_g, 'current_user', {}) or {}
    role = user.get('role')
    if not can_page(role, f'page-{collection}', action):
        return jsonify({'error': '权限不足'}), 403
    return None
```

Then in each write handler, immediately after the function signature and any existing reserved-path check, add the gate. For `create_item` (line ~469):

```python
def create_item(collection):
    denied = _require_page_action(collection, 'create')
    if denied:
        return denied
    # ... existing body
```

For `update_item` (action `'update'`), `delete_item` (`'delete'`), `batch_create_items` (`'create'`), `batch_delete_items` (`'delete'`) — add the same guard with the respective action at the top of each function body.

Note: keep the existing `@write_required` decorators — they remain a coarse first gate (block guests / unauthenticated). The per-page check refines it. `g.current_user` is set by `write_required`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_dynamic_page_permissions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/dynamic.py server/tests/test_dynamic_page_permissions.py
git commit -m "feat(rbac): per-page CRUD gating on dynamic write handlers"
```

---

### Task 3.4: Per-page read gating in dynamic.py — read ops

**Files:**
- Modify: `server/routes/dynamic.py`
- Test: `server/tests/test_dynamic_page_permissions.py`

- [ ] **Step 1: Write failing test**

Append to `server/tests/test_dynamic_page_permissions.py`:

```python
def test_role_with_no_access_cannot_read(setup):
    client, cur, perms = setup
    cur.fetchone.return_value = ('locked', False, 'none')  # default none
    cur.fetchall.side_effect = [[], []]
    resp = client.get('/orders', headers=_token('locked'))
    assert resp.status_code == 403


def test_default_read_allows_list(setup):
    client, cur, perms = setup
    cur.fetchone.return_value = ('guest', False, 'read')
    cur.fetchall.side_effect = [[], []]
    resp = client.get('/orders/some-id', headers=_token('guest'))
    # passes the read gate (may 404 later, but not 403)
    assert resp.status_code != 403
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_dynamic_page_permissions.py -k read -v`
Expected: FAIL — `test_role_with_no_access_cannot_read` returns 200, not 403.

- [ ] **Step 3: Apply read gate**

In `list_items` (line ~295) and `get_item` (line ~450), add right after the function signature:

```python
    denied = _require_page_action(collection, 'read')
    if denied:
        return denied
```

- [ ] **Step 4: Run tests**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_dynamic_page_permissions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/dynamic.py server/tests/test_dynamic_page_permissions.py
git commit -m "feat(rbac): per-page read gating on dynamic read handlers"
```

---

### Task 3.5: Migrate `@admin_required` → `@require_permission` across admin route files

**Files:** (one commit per file recommended; the map below gives the key per file)
- Modify each route file in the capability map at the top of this plan.

- [ ] **Step 1: For each admin route file, swap the decorator**

For every file, change the import line `from auth import admin_required` (or `from auth import login_required, admin_required`) to also import `require_permission`, then replace each `@admin_required` with `@require_permission('<key>')` using the key from the map. Example for `server/routes/backups.py`:

Change:
```python
from auth import admin_required
```
to:
```python
from auth import require_permission
```
Replace every `@admin_required` with `@require_permission('admin.backup')`.

Apply per the map:
- `users.py` → `admin.users`
- `menus.py` → `admin.menus`
- `menu_export.py` → `admin.menus`
- `page_configs.py` → `admin.page_configs`
- `backups.py` → `admin.backup`
- `export_scripts.py` → `admin.export_scripts`
- `api_keys.py` → `admin.api_keys`
- `validation_scripts.py` → `admin.validation_scripts`
- `etl_tasks.py` → `admin.etl_tasks`
- `query.py` → `admin.query`
- `trigger_rules.py` → `admin.trigger_rules`
- `ai.py` → `admin.ai_settings`
- `webhooks.py` → `admin.webhooks`
- `cross_project_dependencies.py` → `admin.dependencies`
- `system_config.py` → `admin.system_config`
- `operation_logs.py` → `admin.operation_logs`
- `project_versions.py` → `admin.project_versions`
- `dashboards.py` → `admin.dashboards`
- `home_widgets.py` → `admin.home_widgets`
- `column_views.py` → `admin.column_views`
- `comments.py` → `admin.comments`

- [ ] **Step 2: Verify no `admin_required` references remain**

Run: `cd server && grep -rln "admin_required" routes/`
Expected: no output (empty). If any file still imports `admin_required` unused, remove the import.

- [ ] **Step 3: Run the full backend suite**

Run: `npm run test:server`
Expected: PASS. Existing admin-route tests use the `admin_token`/`admin_headers` fixtures — but those resolve permissions via `utils.permissions.get_db`, so each such test now needs the role-resolution mock. Where a test 403s unexpectedly, patch its cursor so the role-resolution `fetchone` returns `('admin', True, 'write')`. Update the shared fixtures in `conftest.py` if many tests share them (see Step 4).

- [ ] **Step 4: Make admin fixtures resolve as superuser**

To avoid editing dozens of tests, add an autouse-ish helper. In `server/tests/conftest.py`, after the `admin_token` fixture, add a fixture that callers can include, and document it:

```python
@pytest.fixture
def superuser_resolution(mock_cursor):
    """Make permission resolution treat the role as the admin superuser.

    Prepends the role-resolution fetchone result. Use in tests that hit
    @require_permission-protected routes with the admin token.
    """
    import utils.permissions as perms
    perms.invalidate_cache()
    # Tests that need richer fetchone sequences should set their own side_effect.
    mock_cursor.fetchone.return_value = ('admin', True, 'write')
    yield
```

Apply it to admin-route test modules that broke in Step 3 by adding `superuser_resolution` to their setup, or by setting `mock_cursor.fetchone` appropriately. (This is mechanical per failing test.)

- [ ] **Step 5: Commit**

```bash
git add server/routes/ server/tests/conftest.py
git commit -m "feat(rbac): migrate admin_required to require_permission across routes"
```

---

### Task 3.6: Map relations.py write ops to parent-collection page perms

**Files:**
- Modify: `server/routes/relations.py`
- Test: `server/tests/test_dynamic_page_permissions.py`

- [ ] **Step 1: Inspect relations.py write routes**

Run: `cd server && grep -n "@write_required\|def \|route(" routes/relations.py`
Expected: identifies the write endpoints and which path param carries the collection.

- [ ] **Step 2: Add per-collection update gating**

For each `@write_required` write endpoint in `relations.py` that has a `collection` in scope, add after the signature:

```python
    from utils.permissions import can_page
    from flask import g as _g
    role = (getattr(_g, 'current_user', {}) or {}).get('role')
    if not can_page(role, f'page-{collection}', 'update'):
        return jsonify({'error': '权限不足'}), 403
```

(Relation edits are modifications to the parent record, so they require `update` on the parent collection's page.)

- [ ] **Step 3: Write a guard test**

Append to `server/tests/test_dynamic_page_permissions.py` a test that a read-only role gets 403 on the relations write endpoint (use the actual path discovered in Step 1; example shape):

```python
def test_relations_write_requires_parent_update(setup):
    client, cur, perms = setup
    cur.fetchone.return_value = ('guest', False, 'read')
    cur.fetchall.side_effect = [[], []]
    # Replace with the real relations write path + payload from Step 1:
    resp = client.post('/relations/orders/rec-1/items',
                       data=json.dumps({'relatedIds': []}),
                       content_type='application/json', headers=_token('guest'))
    assert resp.status_code == 403
```

- [ ] **Step 4: Run and commit**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_dynamic_page_permissions.py -v`
Expected: PASS

```bash
git add server/routes/relations.py server/tests/test_dynamic_page_permissions.py
git commit -m "feat(rbac): gate relation writes by parent collection update permission"
```

---

### Task 3.7: Full backend regression

- [ ] **Step 1: Run the whole backend suite**

Run: `npm run test:server`
Expected: PASS (all). Fix any remaining permission-resolution mock gaps per Task 3.5 Step 4.

- [ ] **Step 2: Commit any test fixups**

```bash
git add server/tests/
git commit -m "test(rbac): fix permission-resolution mocks across route tests"
```

---

## Phase 4 — Frontend types, API, store

### Task 4.1: Role & permission types

**Files:**
- Create: `src/types/role.ts`
- Modify: `src/types/user.ts`, `src/types/index.ts` (re-export)

- [ ] **Step 1: Create role types**

Create `src/types/role.ts`:

```typescript
/** 数据页默认权限 */
export type DefaultPageAccess = 'none' | 'read' | 'write'

/** 权限目录项（管理功能开关） */
export interface PermissionCatalogItem {
  key: string
  label: string
  group: string
}

/** 单个数据页 CRUD 配置 */
export interface PagePermission {
  pageId: string
  canRead: boolean
  canCreate: boolean
  canUpdate: boolean
  canDelete: boolean
}

/** 角色（列表项） */
export interface Role {
  id: string
  name: string
  description: string
  isSystem: boolean
  isSuperuser: boolean
  defaultPageAccess: DefaultPageAccess
}

/** 角色详情（编辑器） */
export interface RoleDetail extends Role {
  adminKeys: string[]
  pagePermissions: PagePermission[]
}

/** 后端解析后的当前用户权限集合 */
export interface ResolvedPermissions {
  isSuperuser: boolean
  adminKeys: string[]
  defaultPageAccess: DefaultPageAccess
  pagePerms: Record<string, { read: boolean; create: boolean; update: boolean; delete: boolean }>
}
```

- [ ] **Step 2: Update user types**

In `src/types/user.ts`:
- Change `export type UserRole = 'admin' | 'developer' | 'guest'` to `export type UserRole = string`.
- Add `permissions?: ResolvedPermissions` to `UserInfo` and import it:

```typescript
import type { ResolvedPermissions } from './role'

export type UserRole = string

export interface UserInfo {
  id: string
  username: string
  displayName: string
  role: UserRole
  permissions?: ResolvedPermissions
  createdAt?: string
}
```

- Keep `ROLE_OPTIONS` / `ROLE_LABELS` (now fallback labels for the 3 built-ins; the UI fetches the live list but these stay for display fallback).

- [ ] **Step 3: Re-export from index**

In `src/types/index.ts`, add:

```typescript
export * from './role'
```

- [ ] **Step 4: Type-check**

Run: `npm run build`
Expected: passes type check (or only pre-existing unrelated errors). If `UserRole` narrowing breaks call sites, they're fixed in later tasks; for now ensure `role.ts` and `user.ts` compile.

- [ ] **Step 5: Commit**

```bash
git add src/types/role.ts src/types/user.ts src/types/index.ts
git commit -m "feat(rbac): frontend role & permission types"
```

---

### Task 4.2: Role API client

**Files:**
- Create: `src/api/role.ts`

- [ ] **Step 1: Create the API client**

Create `src/api/role.ts`:

```typescript
import request from './request'
import type { Role, RoleDetail, PermissionCatalogItem } from '@/types'

export function getRoles(): Promise<Role[]> {
  return request.get('/roles')
}

export function getRole(id: string): Promise<RoleDetail> {
  return request.get(`/roles/${id}`)
}

export function getPermissionCatalog(): Promise<PermissionCatalogItem[]> {
  return request.get('/roles/catalog')
}

export function createRole(data: Partial<Role>): Promise<{ id: string; name: string }> {
  return request.post('/roles', data)
}

export function updateRole(id: string, data: Partial<RoleDetail>): Promise<{ message: string }> {
  return request.put(`/roles/${id}`, data)
}

export function deleteRole(id: string): Promise<unknown> {
  return request.delete(`/roles/${id}`)
}
```

(Match the existing `src/api/*.ts` import style — confirm the default export name in `src/api/request.ts` or an existing file like `src/api/user.ts` and mirror it exactly.)

- [ ] **Step 2: Type-check**

Run: `npm run build`
Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add src/api/role.ts
git commit -m "feat(rbac): role API client"
```

---

### Task 4.3: Auth store `can()` / `canPage()` + permissions state

**Files:**
- Modify: `src/stores/auth.ts`
- Test: `src/stores/__tests__/permissions.test.ts`

- [ ] **Step 1: Write failing tests**

Create `src/stores/__tests__/permissions.test.ts`:

```typescript
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, it, expect } from 'vitest'
import { useAuthStore } from '@/stores/auth'
import type { UserInfo } from '@/types'

function userWith(perms: UserInfo['permissions']): UserInfo {
  return { id: 'u', username: 'u', displayName: 'u', role: 'r', permissions: perms }
}

describe('auth store permission helpers', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('superuser can do everything', () => {
    const s = useAuthStore()
    s.user = userWith({ isSuperuser: true, adminKeys: [], defaultPageAccess: 'none', pagePerms: {} })
    expect(s.can('admin.users')).toBe(true)
    expect(s.canPage('page-x', 'delete')).toBe(true)
  })

  it('can() checks adminKeys', () => {
    const s = useAuthStore()
    s.user = userWith({ isSuperuser: false, adminKeys: ['admin.backup'], defaultPageAccess: 'read', pagePerms: {} })
    expect(s.can('admin.backup')).toBe(true)
    expect(s.can('admin.users')).toBe(false)
  })

  it('canPage() falls back to defaultPageAccess', () => {
    const s = useAuthStore()
    s.user = userWith({ isSuperuser: false, adminKeys: [], defaultPageAccess: 'read', pagePerms: {} })
    expect(s.canPage('page-x', 'read')).toBe(true)
    expect(s.canPage('page-x', 'update')).toBe(false)
  })

  it('canPage() uses explicit row over default', () => {
    const s = useAuthStore()
    s.user = userWith({
      isSuperuser: false, adminKeys: [], defaultPageAccess: 'write',
      pagePerms: { 'page-x': { read: true, create: false, update: true, delete: false } },
    })
    expect(s.canPage('page-x', 'create')).toBe(false)
    expect(s.canPage('page-x', 'update')).toBe(true)
    expect(s.canPage('page-y', 'create')).toBe(true) // default write
  })

  it('denies when no permissions present', () => {
    const s = useAuthStore()
    s.user = userWith(undefined)
    expect(s.can('admin.users')).toBe(false)
    expect(s.canPage('page-x', 'read')).toBe(false)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/stores/__tests__/permissions.test.ts`
Expected: FAIL — `s.can is not a function`.

- [ ] **Step 3: Implement helpers in the auth store**

In `src/stores/auth.ts`, add after the existing getters (~line 41) and export them:

```typescript
  type PageAction = 'read' | 'create' | 'update' | 'delete'

  /** 当前用户解析后的权限集合 */
  const permissions = computed(() => user.value?.permissions ?? null)

  /** 是否拥有某个管理功能权限 */
  function can(key: string): boolean {
    const p = permissions.value
    if (!p) return false
    return p.isSuperuser || p.adminKeys.includes(key)
  }

  /** 是否对某数据页拥有某 CRUD 动作权限 */
  function canPage(pageId: string, action: PageAction): boolean {
    const p = permissions.value
    if (!p) return false
    if (p.isSuperuser) return true
    const row = p.pagePerms[pageId]
    if (row) return !!row[action]
    if (p.defaultPageAccess === 'none') return false
    if (p.defaultPageAccess === 'read') return action === 'read'
    return true // 'write'
  }
```

Update `isAdmin` to prefer the superuser flag (keep the role fallback for pre-login states):

```typescript
  const isAdmin = computed(() => user.value?.permissions?.isSuperuser ?? (user.value?.role === 'admin'))
```

Add `permissions`, `can`, `canPage` to the store's `return { ... }`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/stores/__tests__/permissions.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stores/auth.ts src/stores/__tests__/permissions.test.ts
git commit -m "feat(rbac): auth store can()/canPage() permission helpers"
```

---

### Task 4.4: Rework `hasRoutePermission` for `/admin/*`

**Files:**
- Modify: `src/stores/auth.ts`
- Test: `src/stores/__tests__/auth.test.ts`

- [ ] **Step 1: Add the admin path→capability map and write failing test**

Append to `src/stores/__tests__/permissions.test.ts`:

```typescript
describe('hasRoutePermission for admin paths', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('blocks admin path without capability', () => {
    const s = useAuthStore()
    s.token = 't'
    s.user = userWith({ isSuperuser: false, adminKeys: [], defaultPageAccess: 'read', pagePerms: {} })
    expect(s.hasRoutePermission('/admin/users')).toBe(false)
  })

  it('allows admin path with capability', () => {
    const s = useAuthStore()
    s.token = 't'
    s.user = userWith({ isSuperuser: false, adminKeys: ['admin.users'], defaultPageAccess: 'read', pagePerms: {} })
    expect(s.hasRoutePermission('/admin/users')).toBe(true)
  })

  it('superuser allowed on any admin path', () => {
    const s = useAuthStore()
    s.token = 't'
    s.user = userWith({ isSuperuser: true, adminKeys: [], defaultPageAccess: 'none', pagePerms: {} })
    expect(s.hasRoutePermission('/admin/backup')).toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/stores/__tests__/permissions.test.ts`
Expected: FAIL — `/admin/users` currently allowed only via `role === 'admin'` fallback.

- [ ] **Step 3: Add the map and rework the function**

In `src/stores/auth.ts`, add near the top of the store body:

```typescript
  /** /admin 路径 → 所需管理功能权限 key */
  const ADMIN_PATH_PERMISSION: Record<string, string> = {
    '/admin/menu': 'admin.menus',
    '/admin/menu-export': 'admin.menus',
    '/admin/page-config': 'admin.page_configs',
    '/admin/users': 'admin.users',
    '/admin/roles': 'admin.roles',
    '/admin/operation-log': 'admin.operation_logs',
    '/admin/backup': 'admin.backup',
    '/admin/factory-reset': 'admin.backup',
    '/admin/export-scripts': 'admin.export_scripts',
    '/admin/api-keys': 'admin.api_keys',
    '/admin/validation-scripts': 'admin.validation_scripts',
    '/admin/etl-tasks': 'admin.etl_tasks',
    '/admin/query': 'admin.query',
    '/admin/trigger-rules': 'admin.trigger_rules',
    '/admin/ai-settings': 'admin.ai_settings',
    '/admin/webhook-settings': 'admin.webhooks',
    '/admin/dependency-manager': 'admin.dependencies',
    '/admin/system-settings': 'admin.system_config',
  }
```

Rework `hasRoutePermission`:

```typescript
  function hasRoutePermission(path: string): boolean {
    if (!user.value) return false
    if (path === '/home' || path === '/') return true

    // 管理页：按所需能力 key 判定
    const required = ADMIN_PATH_PERMISSION[path]
    if (required) return can(required)

    const menuStore = useMenuStore()
    const menu = menuStore.getMenuByPath(path)
    if (menu) {
      const menuRoles = menu.roles || []
      // 空白名单或包含当前角色 slug 即放行；超管始终放行
      return permissions.value?.isSuperuser || menuRoles.length === 0 || menuRoles.includes(user.value.role)
    }

    if (path.startsWith('/page/')) return true
    // 未匹配菜单的其他 /admin 路径：默认仅超管
    if (path.startsWith('/admin/')) return permissions.value?.isSuperuser ?? false
    return permissions.value?.isSuperuser ?? false
  }
```

- [ ] **Step 4: Run tests**

Run: `npx vitest run src/stores/__tests__/permissions.test.ts src/stores/__tests__/auth.test.ts`
Expected: PASS. If `auth.test.ts` asserts old `hasRoutePermission` behavior (admin-only default via role string), update those assertions to use the superuser flag.

- [ ] **Step 5: Commit**

```bash
git add src/stores/auth.ts src/stores/__tests__/
git commit -m "feat(rbac): route guard uses capability keys for /admin paths"
```

---

### Task 4.5: Fetch permissions on session restore

**Files:**
- Modify: `src/stores/auth.ts`

- [ ] **Step 1: Confirm `fetchCurrentUser` stores permissions**

`fetchCurrentUser` already does `user.value = userInfo` from `getMeApi()`. Since `/auth/me` now returns `permissions`, no code change is needed — but verify `getMeApi`'s return type includes `permissions`. In `src/api/auth.ts`, ensure the `getCurrentUser` return type is `Promise<UserInfo>` (it maps the response). If it strips fields, update it to pass `permissions` through.

- [ ] **Step 2: Verify by reading src/api/auth.ts**

Run: open `src/api/auth.ts` and confirm `login` and `getCurrentUser` return the full object including `permissions`. Adjust mapping if it explicitly picks fields.

- [ ] **Step 3: Type-check & commit (if changed)**

Run: `npm run build`
Expected: passes.

```bash
git add src/api/auth.ts
git commit -m "feat(rbac): pass permissions through auth API responses"
```

---

## Phase 5 — Frontend menu gating & route

### Task 5.1: Register `/admin/roles` route

**Files:**
- Modify: `src/router/index.ts`

- [ ] **Step 1: Add the route**

In `src/router/index.ts`, add a child route alongside the other `admin/*` routes (after `admin/users`, ~line 102):

```typescript
      {
        path: 'admin/roles',
        name: 'RoleManager',
        component: () => import('@/views/admin/RoleManager.vue'),
        meta: {
          title: '角色权限',
          icon: 'Lock',
        },
      },
```

- [ ] **Step 2: Type-check**

Run: `npm run build`
Expected: fails only because `RoleManager.vue` doesn't exist yet — that's expected; it's created in Task 6.1. Skip the build check here or create a stub. Create a minimal stub now to keep the build green:

```bash
mkdir -p src/views/admin
```

Create `src/views/admin/RoleManager.vue` stub:

```vue
<template><div>角色权限</div></template>
<script setup lang="ts"></script>
```

Run: `npm run build` → passes.

- [ ] **Step 3: Commit**

```bash
git add src/router/index.ts src/views/admin/RoleManager.vue
git commit -m "feat(rbac): register /admin/roles route (stub view)"
```

---

### Task 5.2: Add a "角色权限" menu entry + seed migration

**Files:**
- Modify: `server/init_db.py`, `server/seed_data.py`

- [ ] **Step 1: Add seed menu entry**

In `server/seed_data.py` MENUS list, add under 平台管理 (`menu-3-a`), after 用户管理 (`menu-3-3`, order 3):

```python
    {"id": "menu-3-12", "name": "角色权限", "icon": "Lock", "pageId": None, "parentId": "menu-3-a", "order": 6, "path": "/admin/roles", "roles": ["admin"]},
```

- [ ] **Step 2: Add idempotent migration**

In `server/init_db.py`, after the AI 配置 menu migration (find `menu-3-11`) or near other menu migrations, add:

```python
        # Migration: add 角色权限 menu if missing
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-12'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
                "VALUES ('menu-3-12', %s, 'Lock', NULL, 'menu-3-a', 6, '/admin/roles', %s)",
                ('角色权限', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added 角色权限 menu.")
```

- [ ] **Step 3: Run init_db and verify**

Run: `cd server && python init_db.py`
Then verify: `cd server && python -c "import psycopg2; from config import DB_CONFIG; c=psycopg2.connect(**DB_CONFIG); cur=c.cursor(); cur.execute(\"SELECT path FROM menus WHERE id='menu-3-12'\"); print(cur.fetchone())"`
Expected: `('/admin/roles',)`

- [ ] **Step 4: Commit**

```bash
git add server/init_db.py server/seed_data.py
git commit -m "feat(rbac): add 角色权限 menu entry"
```

---

### Task 5.3: Gate SideMenu admin links by capability

**Files:**
- Modify: `src/components/layout/SideMenu.vue`

- [ ] **Step 1: Inspect how SideMenu filters menus**

Run: open `src/components/layout/SideMenu.vue` and find where the menu tree is rendered/filtered (it currently relies on `menu.roles` from the menu store, filtered by role in `src/stores/menu.ts`).

- [ ] **Step 2: Ensure menu-store role filtering supports custom slugs**

Run: open `src/stores/menu.ts`, find the role-filter predicate. It compares `menu.roles.includes(role)`. Since `role` is now any slug and `menu.roles` holds slugs, custom roles work. Add superuser bypass: if `authStore.isAdmin` (superuser), show all. Confirm/adjust the predicate:

```typescript
// inside the filter
const role = authStore.userRole
const isSuper = authStore.isAdmin
return isSuper || !menu.roles || menu.roles.length === 0 || menu.roles.includes(role!)
```

- [ ] **Step 3: Manual verification (deferred to Phase 7 smoke)**

No isolated unit test here (covered by menu store tests). Run existing menu store tests:

Run: `npx vitest run src/stores/__tests__/menu.test.ts`
Expected: PASS (update any assertion that hardcoded the 3-role behavior to include the superuser bypass).

- [ ] **Step 4: Commit**

```bash
git add src/components/layout/SideMenu.vue src/stores/menu.ts src/stores/__tests__/menu.test.ts
git commit -m "feat(rbac): superuser bypass + custom-slug support in menu filtering"
```

---

## Phase 6 — Role Management UI & User Manager

### Task 6.1: Role store

**Files:**
- Create: `src/stores/role.ts`

- [ ] **Step 1: Create the store**

Create `src/stores/role.ts`:

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getRoles, getRole, getPermissionCatalog, createRole, updateRole, deleteRole,
} from '@/api/role'
import type { Role, RoleDetail, PermissionCatalogItem } from '@/types'

export const useRoleStore = defineStore('role', () => {
  const roles = ref<Role[]>([])
  const catalog = ref<PermissionCatalogItem[]>([])
  const loading = ref(false)

  async function loadRoles(): Promise<void> {
    loading.value = true
    try {
      roles.value = await getRoles()
    } finally {
      loading.value = false
    }
  }

  async function loadCatalog(): Promise<void> {
    if (catalog.value.length) return
    catalog.value = await getPermissionCatalog()
  }

  function fetchRole(id: string): Promise<RoleDetail> {
    return getRole(id)
  }

  async function saveRole(id: string, data: Partial<RoleDetail>): Promise<void> {
    await updateRole(id, data)
    await loadRoles()
  }

  async function addRole(data: Partial<Role>): Promise<{ id: string; name: string }> {
    const res = await createRole(data)
    await loadRoles()
    return res
  }

  async function removeRole(id: string): Promise<void> {
    await deleteRole(id)
    await loadRoles()
  }

  return { roles, catalog, loading, loadRoles, loadCatalog, fetchRole, saveRole, addRole, removeRole }
})
```

- [ ] **Step 2: Type-check & commit**

Run: `npm run build`
Expected: passes.

```bash
git add src/stores/role.ts
git commit -m "feat(rbac): role management store"
```

---

### Task 6.2: RoleManager.vue — list + admin-feature matrix

**Files:**
- Modify: `src/views/admin/RoleManager.vue` (replace the stub)

- [ ] **Step 1: Implement list + admin permission matrix + page perms + default access**

Replace `src/views/admin/RoleManager.vue` with:

```vue
<template>
  <div class="role-manager">
    <el-card class="role-list-card">
      <template #header>
        <div class="card-header">
          <span>角色</span>
          <el-button type="primary" size="small" @click="openCreate">新建角色</el-button>
        </div>
      </template>
      <el-menu :default-active="selectedId" @select="selectRole">
        <el-menu-item v-for="r in roleStore.roles" :key="r.id" :index="r.id">
          <span>{{ r.name }}</span>
          <el-tag v-if="r.isSuperuser" type="danger" size="small" style="margin-left:8px">超管</el-tag>
          <el-tag v-else-if="r.isSystem" type="info" size="small" style="margin-left:8px">内置</el-tag>
        </el-menu-item>
      </el-menu>
    </el-card>

    <el-card v-if="detail" class="role-editor-card">
      <template #header>
        <div class="card-header">
          <span>{{ detail.name }} — 权限配置</span>
          <div>
            <el-button v-if="!detail.isSystem" type="danger" size="small" @click="onDelete">删除角色</el-button>
            <el-button type="primary" size="small" :disabled="detail.isSuperuser" @click="onSave">保存</el-button>
          </div>
        </div>
      </template>

      <el-alert v-if="detail.isSuperuser" type="info" :closable="false"
        title="超级管理员拥有全部权限，不可修改。" style="margin-bottom:16px" />

      <el-tabs v-model="activeTab">
        <el-tab-pane label="管理功能" name="admin">
          <div v-for="group in groupedCatalog" :key="group.name" class="perm-group">
            <h4>{{ group.name }}</h4>
            <el-checkbox
              v-for="item in group.items" :key="item.key"
              :model-value="adminKeys.has(item.key)"
              :disabled="detail.isSuperuser"
              @change="(v: boolean) => toggleAdminKey(item.key, v)"
            >{{ item.label }}</el-checkbox>
          </div>
        </el-tab-pane>

        <el-tab-pane label="数据页权限" name="pages">
          <el-form-item label="未配置数据页默认">
            <el-radio-group v-model="defaultPageAccess" :disabled="detail.isSuperuser">
              <el-radio value="none">无</el-radio>
              <el-radio value="read">只读</el-radio>
              <el-radio value="write">读写</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-table :data="pageRows" border height="420">
            <el-table-column prop="name" label="数据页" />
            <el-table-column label="读" width="70" align="center">
              <template #default="{ row }">
                <el-checkbox v-model="row.canRead" :disabled="detail.isSuperuser" /></template>
            </el-table-column>
            <el-table-column label="增" width="70" align="center">
              <template #default="{ row }">
                <el-checkbox v-model="row.canCreate" :disabled="detail.isSuperuser" /></template>
            </el-table-column>
            <el-table-column label="改" width="70" align="center">
              <template #default="{ row }">
                <el-checkbox v-model="row.canUpdate" :disabled="detail.isSuperuser" /></template>
            </el-table-column>
            <el-table-column label="删" width="70" align="center">
              <template #default="{ row }">
                <el-checkbox v-model="row.canDelete" :disabled="detail.isSuperuser" /></template>
            </el-table-column>
          </el-table>
          <p class="hint">未在表中勾选的数据页按上面的“默认”计算。仅保存有任意勾选的行。</p>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="createVisible" title="新建角色" width="420px">
      <el-form :model="createForm" label-width="90px">
        <el-form-item label="名称"><el-input v-model="createForm.name" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="createForm.description" /></el-form-item>
        <el-form-item label="默认数据页">
          <el-radio-group v-model="createForm.defaultPageAccess">
            <el-radio value="none">无</el-radio>
            <el-radio value="read">只读</el-radio>
            <el-radio value="write">读写</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoleStore } from '@/stores/role'
import { usePageConfigStore } from '@/stores/pageConfig'
import type { RoleDetail, DefaultPageAccess } from '@/types'

const roleStore = useRoleStore()
const pageConfigStore = usePageConfigStore()

const selectedId = ref('')
const detail = ref<RoleDetail | null>(null)
const activeTab = ref('admin')
const adminKeys = ref<Set<string>>(new Set())
const defaultPageAccess = ref<DefaultPageAccess>('read')
const pageRows = ref<Array<{ pageId: string; name: string; canRead: boolean; canCreate: boolean; canUpdate: boolean; canDelete: boolean }>>([])

const createVisible = ref(false)
const createForm = ref<{ name: string; description: string; defaultPageAccess: DefaultPageAccess }>({
  name: '', description: '', defaultPageAccess: 'read',
})

const groupedCatalog = computed(() => {
  const groups: Record<string, typeof roleStore.catalog> = {}
  for (const item of roleStore.catalog) {
    (groups[item.group] ||= []).push(item)
  }
  return Object.entries(groups).map(([name, items]) => ({ name, items }))
})

async function selectRole(id: string): Promise<void> {
  selectedId.value = id
  const d = await roleStore.fetchRole(id)
  detail.value = d
  adminKeys.value = new Set(d.adminKeys)
  defaultPageAccess.value = d.defaultPageAccess
  const configured = new Map(d.pagePermissions.map(p => [p.pageId, p]))
  // build rows from all page configs
  const pages = pageConfigStore.allConfigs ?? []
  pageRows.value = pages.map((pc: { id: string; name: string }) => {
    const c = configured.get(pc.id)
    return {
      pageId: pc.id, name: pc.name,
      canRead: c?.canRead ?? false, canCreate: c?.canCreate ?? false,
      canUpdate: c?.canUpdate ?? false, canDelete: c?.canDelete ?? false,
    }
  })
}

function toggleAdminKey(key: string, on: boolean): void {
  if (on) adminKeys.value.add(key)
  else adminKeys.value.delete(key)
}

async function onSave(): Promise<void> {
  if (!detail.value) return
  const pagePermissions = pageRows.value
    .filter(r => r.canRead || r.canCreate || r.canUpdate || r.canDelete)
    .map(r => ({ pageId: r.pageId, canRead: r.canRead, canCreate: r.canCreate, canUpdate: r.canUpdate, canDelete: r.canDelete }))
  await roleStore.saveRole(detail.value.id, {
    adminKeys: [...adminKeys.value],
    defaultPageAccess: defaultPageAccess.value,
    pagePermissions,
  })
  ElMessage.success('已保存')
}

function openCreate(): void {
  createForm.value = { name: '', description: '', defaultPageAccess: 'read' }
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!createForm.value.name.trim()) { ElMessage.warning('请输入名称'); return }
  const res = await roleStore.addRole(createForm.value)
  createVisible.value = false
  ElMessage.success('已创建')
  await selectRole(res.id)
}

async function onDelete(): Promise<void> {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm(`确定删除角色「${detail.value.name}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  await roleStore.removeRole(detail.value.id)
  detail.value = null
  selectedId.value = ''
  ElMessage.success('已删除')
}

onMounted(async () => {
  await Promise.all([roleStore.loadRoles(), roleStore.loadCatalog(), pageConfigStore.loadAllConfigs?.()])
  if (roleStore.roles.length) await selectRole(roleStore.roles[0].id)
})
</script>

<style scoped lang="scss">
.role-manager { display: flex; gap: 16px; height: 100%; }
.role-list-card { width: 240px; flex-shrink: 0; }
.role-editor-card { flex: 1; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.perm-group { margin-bottom: 16px; h4 { margin: 8px 0; } }
.hint { color: #909399; font-size: 12px; margin-top: 8px; }
</style>
```

- [ ] **Step 2: Confirm pageConfig store exposes a list of all page configs**

Run: open `src/stores/pageConfig.ts` and confirm there is a property/method returning all page configs with `{ id, name }`. If the name differs from `allConfigs` / `loadAllConfigs`, update the two references in RoleManager.vue to match the real API. If none exists, add a thin `getAllConfigs()` that calls the existing page-config API (`GET /pageConfigs`).

- [ ] **Step 3: Type-check**

Run: `npm run build`
Expected: passes (fix any mismatched store member names found in Step 2).

- [ ] **Step 4: Commit**

```bash
git add src/views/admin/RoleManager.vue src/stores/pageConfig.ts
git commit -m "feat(rbac): role manager UI (admin matrix, page CRUD grid, default access)"
```

---

### Task 6.3: UserManager uses fetched roles

**Files:**
- Modify: `src/views/admin/UserManager.vue`

- [ ] **Step 1: Replace hardcoded ROLE_OPTIONS with fetched roles**

In `src/views/admin/UserManager.vue` `<script setup>`:
- Import the role store: `import { useRoleStore } from '@/stores/role'`
- Add `const roleStore = useRoleStore()` and load on mount: in the existing `onMounted`, add `roleStore.loadRoles()`.
- Replace the `<el-option v-for="opt in ROLE_OPTIONS">` loop's source with `roleStore.roles`:

```vue
            <el-option
              v-for="opt in roleStore.roles"
              :key="opt.id"
              :label="opt.name"
              :value="opt.id"
            />
```

- For the table role tag label, replace `ROLE_LABELS[row.role as UserRole] || row.role` with a lookup over fetched roles:

```typescript
function roleLabel(roleId: string): string {
  return roleStore.roles.find(r => r.id === roleId)?.name || roleId
}
```

and in template: `{{ roleLabel(row.role) }}`.

- `getRoleTagType` can stay (defaults to `info` for unknown custom roles).

- [ ] **Step 2: Type-check**

Run: `npm run build`
Expected: passes.

- [ ] **Step 3: Run existing UserManager-related tests**

Run: `npx vitest run src/api/__tests__/user.test.ts`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/views/admin/UserManager.vue
git commit -m "feat(rbac): user manager role dropdown uses live roles"
```

---

## Phase 7 — Data page button gating & smoke

### Task 7.1: Gate New/Edit/Delete buttons in DynamicPage

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`

- [ ] **Step 1: Compute per-page permissions**

In `src/views/dynamic/DynamicPage.vue` `<script setup>`, add (the page's `pageId` is already available from the route/config — confirm the local variable name, commonly `pageId`):

```typescript
import { useAuthStore } from '@/stores/auth'
const authStore = useAuthStore()

const canCreate = computed(() => authStore.canPage(`page-${collection.value}`, 'create'))
const canUpdate = computed(() => authStore.canPage(`page-${collection.value}`, 'update'))
const canDelete = computed(() => authStore.canPage(`page-${collection.value}`, 'delete'))
```

(Use the existing reactive that holds the collection name — confirm whether it's `collection`, `pageId`, or derived; the page config id is `page-<collection>`.)

- [ ] **Step 2: Bind to the toolbar/table actions**

Add `v-if="canCreate"` to the "新增" button, and pass `:can-update="canUpdate"` / `:can-delete="canDelete"` down to `DataTable` (or guard the row action buttons directly if rendered in DynamicPage). For DataTable row buttons, wrap edit with `v-if` on the passed prop and delete likewise. Add the props to `DataTable.vue`'s `defineProps` with defaults `true`.

- [ ] **Step 3: Type-check**

Run: `npm run build`
Expected: passes.

- [ ] **Step 4: Run dynamic page tests if present**

Run: `npx vitest run src/views/dynamic`
Expected: PASS (or no tests collected).

- [ ] **Step 5: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue src/components/dynamic-form/DataTable.vue
git commit -m "feat(rbac): gate data-page action buttons by page CRUD permission"
```

---

### Task 7.2: Full frontend test suite + manual smoke

- [ ] **Step 1: Run all frontend tests**

Run: `npm run test`
Expected: PASS. Fix any assertions broken by the `UserRole = string` change or the `isAdmin`/`hasRoutePermission` rework.

- [ ] **Step 2: Run full type check + build**

Run: `npm run build`
Expected: PASS.

- [ ] **Step 3: Manual smoke (requires running app)**

Run: `npm run dev:all`, then:
1. Log in as `admin/admin123` → confirm all admin menus visible, `/admin/roles` opens.
2. Create a custom role "质检员" with `admin.query` only + default page access "read", and a page with full CRUD.
3. Create a user with that role; log in as them → confirm only the granted admin link shows, data pages are read-only except the configured page, and forbidden writes return 403.
4. Edit the role to add `admin.backup`; without re-login, confirm the change applies after the next `/auth/me` (reload) — server resolves fresh.

- [ ] **Step 4: Final commit (any smoke fixups)**

```bash
git add -A
git commit -m "fix(rbac): smoke-test fixups"
```

---

## Self-Review Notes

- **Spec coverage:** menu visibility (Task 5.3), per-page CRUD (3.3/3.4/7.1), admin-feature toggles (3.5/6.2), admin superuser invariant (0.3, 2.1 guards), default read-not-write (1.2 `_default_allows`, 0.3 seed), permissions payload (3.1/4.x), role management UI (6.x), menu-slug scrub on delete (2.1), cache invalidation (1.2/2.1), MCP note (out of scope — verify-only, no task; flag during 3.7 if MCP gates on role).
- **Deferred verification points (resolve during implementation):** exact `request` default-export name in `src/api/*` (4.2), pageConfig store member names (6.2), DynamicPage collection variable name (7.1), relations.py write route shapes (3.6). Each task instructs the engineer to confirm against the real file before coding.
- **No field-level / role-hierarchy / row-level** work — explicitly out of scope per spec §1.
