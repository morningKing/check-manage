# 自定义角色与权限系统（Custom Roles RBAC）设计

- 日期：2026-06-02
- 状态：已评审，待实现
- 作者：jayKim + Claude

## 1. 背景与目标

当前系统只有 3 个**硬编码**角色，权限模型是固定的三级阶梯（admin > developer > guest）加上每个菜单的角色白名单：

- 角色类型硬编码为 TS 联合类型 `UserRole = 'admin' | 'developer' | 'guest'`（`src/types/user.ts:8`），后端在 `server/routes/users.py:51` 校验 `role not in ('admin','developer','guest')`。
- 后端通过 `server/auth.py` 的 4 个装饰器鉴权：`login_required`、`write_required`（拦截 guest）、`admin_required`（仅 admin）、`api_key_required`。共 **~105 处装饰器使用，分布在 23 个路由文件**。
- 角色被写入 **JWT payload**（`create_token`）。
- **菜单 RBAC**：`menus` 表每行有 `roles` JSONB 白名单，`hasRoutePermission` 检查 `menuRoles.includes(role)`（`src/stores/auth.ts:97`）。
- **管理页**（`/admin/*`）未在路由 meta 标注，靠兜底规则保护（"未匹配菜单的路径默认仅 admin"，`src/stores/auth.ts:105`）。

**目标**：让角色可自定义，并在三个粒度上影响功能权限：

1. **菜单可见性** —— 角色能看到哪些菜单/页面。
2. **数据页 CRUD 权限** —— 对每个数据页/集合分别控制 读/增/改/删。
3. **管理功能开关** —— 每个 `/admin` 管理模块可逐个授权给角色。

**非目标（YAGNI）**：字段级权限、角色继承/层级、行级（per-record）权限、限时授权、角色变更审批流。

## 2. 关键决策

| 决策点 | 结论 |
|--------|------|
| 权限粒度 | 菜单可见性 + 数据页 CRUD + 管理功能开关（无字段级） |
| 内置角色 | `admin` = 永久超管（拥有全部权限、不可删除、不可降权）；`developer`/`guest` = 可编辑预置角色；允许新增任意自定义角色 |
| 未配置数据页的默认权限 | **可读、不可写**（角色级 `default_page_access` 兜底） |
| 管理功能默认 | **默认拒绝**（default-deny）——只有超管 + 被显式授权的角色可用 |
| JWT 内容 | 仅携带角色 slug；权限每请求从 DB（带缓存）解析，编辑角色后立即生效，无需重新登录 |

## 3. 数据模型

新增 3 张表，复用现有 `menus.roles`。

```sql
-- 角色表
roles
  id            text PRIMARY KEY      -- slug: 'admin','developer','guest','role-<uuid>'
  name          text NOT NULL         -- 显示名，如 "质检员"
  description   text
  is_system     bool NOT NULL         -- 内置 3 角色为 true（不可删除）
  is_superuser  bool NOT NULL         -- 仅 'admin' 为 true（绕过所有检查）
  default_page_access text NOT NULL   -- 'none' | 'read' | 'write'，未配置数据页的兜底
  created_at    timestamptz
  updated_at    timestamptz

-- 管理功能授权（默认拒绝：有行 = 授权）
role_permissions
  role_id        text REFERENCES roles(id) ON DELETE CASCADE
  permission_key text                 -- 如 'admin.users', 'admin.backup'
  PRIMARY KEY (role_id, permission_key)

-- 每数据页 CRUD（无行 ⇒ 走 default_page_access 兜底）
role_page_permissions
  role_id     text REFERENCES roles(id) ON DELETE CASCADE
  page_id     text                     -- 'page-<collection>'
  can_read    bool NOT NULL DEFAULT true
  can_create  bool NOT NULL DEFAULT false
  can_update  bool NOT NULL DEFAULT false
  can_delete  bool NOT NULL DEFAULT false
  PRIMARY KEY (role_id, page_id)
```

**说明：**

- `users.role` 保持 `text` 列，去掉 enum/CHECK 约束；改为**应用层校验**"存在于 `roles` 表"（与本仓库业务数据不加 FK 的风格一致）。
- **菜单可见性**继续用 `menus.roles` JSONB（角色 slug 数组），**零 schema 改动**，自定义 slug 直接可用。
- **数据页默认解析**：`role_page_permissions` 无对应行时，按角色的 `default_page_access` 兜底（`'write'` ⇒ 增改删读全开，`'read'` ⇒ 仅读，`'none'` ⇒ 全拒）。新建数据页无需配置即对各角色按其默认值生效。
- **超管**（`admin`）：`is_superuser=true` 短路所有检查为允许，无需在权限表中存任何行。

**内置角色种子（精确复刻当前行为）：**

| 角色 | is_superuser | default_page_access | 管理功能 |
|------|--------------|---------------------|----------|
| `admin` | true | write | 全部（超管短路） |
| `developer` | false | **write** | 无（今日 developer 可写全部数据但看不到管理页） |
| `guest` | false | **read** | 无 |

`admin.roles` 权限种子仅授予 `admin`。

## 4. 后端实施

### 4.1 解析模块 `server/utils/permissions.py`（单一事实来源）

```python
# 进程级缓存：role_id -> 解析后的权限集合；任何角色/权限写操作后失效
def get_role_perms(role_id) -> {
    'is_superuser': bool,
    'admin_keys': set[str],                  # 来自 role_permissions
    'default_page_access': 'none'|'read'|'write',
    'page_perms': { page_id: {read,create,update,delete} },  # 来自 role_page_permissions
}

def can_admin(role_id, key) -> bool          # superuser ⇒ True；否则 key in admin_keys
def can_page(role_id, page_id, action) -> bool   # superuser ⇒ True；有行用行，否则按 default_page_access
def invalidate_cache(role_id=None)           # 角色管理写操作调用
```

缓存为进程级 dict + 锁；每次角色/权限变更失效。JWT 仅含角色 slug，故每请求从缓存解析 —— 编辑角色后立即生效，无需重新登录。

### 4.2 新装饰器 `@require_permission('key')`（`server/auth.py`）

复用现有 JWT/token 流程：校验 JWT → 载入 `g.current_user` → `can_admin(role, key)` 为真则放行，否则 `403 权限不足`。

### 4.3 ~105 处装饰器迁移（三类，机械替换）

| 旧 | 新 | 约数 |
|----|----|------|
| `/admin` 模块上的 `@admin_required` | `@require_permission('admin.<feature>')` | ~70 |
| **dynamic.py** CRUD 上的 `@write_required` | 处理函数内 `can_page(role, page-<collection>, action)`，否则 `403` | 5 |
| **其他**写路由（comments/dashboards/home-widgets/column-views/project-versions/relations…）上的 `@write_required` | `@require_permission('<feature>')` 映射到该模块能力 | ~30 |
| `@login_required` | 不变 | — |

### 4.4 管理功能能力目录（~22 + 1 keys）

在 `permissions.py` 定义一次 `PERMISSION_CATALOG`（key + 中文标签 + 分组），角色管理 UI 据此渲染开关：

```
admin.users, admin.menus, admin.page_configs, admin.backup, admin.export_scripts,
admin.api_keys, admin.validation_scripts, admin.etl_tasks, admin.query,
admin.trigger_rules, admin.ai_settings, admin.webhooks, admin.dependencies,
admin.menu_export, admin.system_config, admin.factory_reset, admin.operation_logs,
admin.project_versions, admin.dashboards, admin.home_widgets, admin.column_views,
admin.comments, admin.roles
```

### 4.5 数据页 CRUD 强制（`routes/dynamic.py`）

- 写处理函数（`create_item`、`update_item`、`delete_item`、`batch_create_items`、`batch_delete_items`）调用 `can_page(role, f'page-{collection}', action)`。
- 读处理函数（`list_items`、`get_item`）调用 `read` 检查 —— 这为数据页新增了原本不存在的读取门禁，但因默认 `read=true`（default-allow），对现有角色无破坏。

### 4.6 记录维度写操作映射

`comments`、`relations` 等"记录维度"写操作，映射到其所属集合的 **页 CRUD 权限**（有集合上下文时）；独立功能（dashboards、home-widgets）给独立 admin key。实现计划中逐一列举每个路由的精确映射。

## 5. 前端实施

### 5.1 权限载荷

`/auth/login` 与 `/auth/me` 响应新增 `permissions` 对象：

```jsonc
{
  "id": "...", "username": "...", "displayName": "...", "role": "developer",
  "permissions": {
    "isSuperuser": false,
    "adminKeys": ["admin.backup", "admin.etl_tasks"],
    "defaultPageAccess": "write",
    "pagePerms": { "page-orders": { "read": true, "create": true, "update": false, "delete": false } }
  }
}
```

`pagePerms` 仅含**显式配置**过的页（体积可控）；其余在前端按 `defaultPageAccess` 兜底，与服务端一致。

### 5.2 Auth store（`src/stores/auth.ts`）解析助手

替换散落的 `isAdmin` 检查：

```ts
can(key: string): boolean          // superuser || adminKeys.includes(key)
canPage(pageId, action): boolean   // superuser || pagePerms[pageId]?.[action] ?? 默认兜底
isAdmin                            // = permissions.isSuperuser（保留向后兼容；管理菜单分区改用 can('admin.*')）
```

- **路由守卫**（`hasRoutePermission`）：对 `/admin/*` 路径映射 path→能力 key 并 `can(key)`；数据/菜单路径仍走 `menus.roles` 白名单（现已支持自定义 slug）。
- **SideMenu**：每个管理链接按其 `can('admin.x')` 条件渲染；角色无任何 admin key 时整组"系统管理"隐藏。
- **DynamicPage / DataTable / DynamicForm**：新建/编辑/删除按钮按 `canPage(pageId, action)` 控制；只读角色看到只读表格。（服务端仍是权威，这里仅 UX。）

### 5.3 角色管理 UI

新增管理页 `/admin/roles`（`src/views/admin/RoleManager.vue`），自身受 `can('admin.roles')` 保护（目录第 23 个 key，仅种子给 admin）：

- **左侧**：角色列表（系统角色徽标、超管徽标且锁定）；"新建角色"按钮。
- **右侧**：选中角色的编辑器，三个分区：
  1. **管理功能** —— 按分组的 `PERMISSION_CATALOG` 复选矩阵。
  2. **数据页权限** —— 所有数据页（来自 page_configs）× {读/增/改/删} 复选表，外加该角色未配置页的 `defaultPageAccess` 选择器。
  3. **菜单可见性** —— 菜单树，每节点开关（写入 `menus.roles`）。
- 编辑**超管**角色：全部显示为勾选且禁用。删除有用户在用的角色被阻止（要求先清空分配用户）。

### 5.4 新增 API 与前端文件

后端 `server/routes/roles.py`（蓝图 `roles_bp`，受 `admin.roles` 保护）：

- `GET /roles`、`POST /roles`、`PUT /roles/:id`、`DELETE /roles/:id`
- `GET /roles/catalog` —— 渲染用权限目录
- `GET/PUT /roles/:id/page-permissions`
- 菜单可见性 tab 复用现有菜单 API

蓝图注册顺序：在 `dynamic_bp`（catch-all）之前注册 `roles_bp`。

前端：`src/api/role.ts`、`src/stores/role.ts`、`src/types/role.ts`。`UserManager.vue` 的角色下拉从硬编码 `ROLE_OPTIONS` 改为拉取角色列表。

## 6. 迁移、边界情况与测试

### 6.1 DB 迁移 / 种子（扩展 `server/init_db.py` + `seed_data.py`）

- 创建 3 张表 + `roles.default_page_access` 列。
- 种子 `admin`（超管）、`developer`（`default_page_access='write'`，无 admin key）、`guest`（`'read'`，无 admin key），精确复刻今日行为。`admin.roles` 仅给 admin。
- 去掉 `users.role` 上的 enum/CHECK；改应用层校验（避免破坏现有行，不加硬 FK）。
- 幂等（`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`），重跑 `init_db.py` 安全。

### 6.2 边界情况

- **不能把自己锁死**：超管 `admin` 不可删除、永远全权限；`admin.roles` 与 `admin.users` 不可从其移除。
- **删除被用户引用的角色** → 阻止并给出清晰错误（列出受影响用户）。
- **删除自定义角色** 时其 slug 可能存在于 `menus.roles` 数组 → 事务内从所有 `menus.roles` 清除该 slug。
- **缓存一致性**：每次角色/权限写调用 `invalidate_cache`；载荷每请求解析，编辑后无陈旧授权。
- **API keys**（`api_key_required`）不受影响 —— 独立的机器鉴权路径，超出范围。
- **MCP server RBAC**（从 session token 推导 user/role）读 `users.role`；自定义角色就是字符串。若其做任何门禁，应经同一 `permissions` 助手解析 —— 实现计划中**核查**，不扩大范围。

### 6.3 测试

- **后端（pytest）**：`permissions.py` 解析矩阵（超管短路、默认兜底、显式授权、缓存失效）；`require_permission` 的 403 路径；dynamic.py 每页 CRUD 门禁；角色 CRUD + 防自锁；删除时菜单 slug 清除。沿用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`。
- **前端（vitest）**：auth-store `can`/`canPage` 解析 + 默认兜底；`/admin/*` 的 `hasRoutePermission`；RoleManager 交互（按 CLAUDE.md 模式 stub Element Plus）；UserManager 拉取角色下拉。
- **回归**：现有 `auth.test.ts` / `user.test.ts` 更新以适配新 `permissions` 载荷；断言 3 个种子角色复刻当前行为。

## 7. 关键文件清单

**后端**：`server/utils/permissions.py`（新）、`server/auth.py`（新装饰器）、`server/routes/roles.py`（新）、`server/routes/dynamic.py`（CRUD 门禁）、`server/routes/auth.py`（载荷）、`server/routes/users.py`（角色校验）、`server/init_db.py` + `server/seed_data.py`（迁移/种子）、`server/app.py`（注册 `roles_bp`）、以及 23 个路由文件的装饰器迁移。

**前端**：`src/stores/auth.ts`、`src/router/index.ts`（守卫）、`src/components/layout/SideMenu.vue`、`src/views/dynamic/DynamicPage.vue` + `DataTable`/`DynamicForm`、`src/views/admin/RoleManager.vue`（新）、`src/views/admin/UserManager.vue`、`src/api/role.ts`（新）、`src/stores/role.ts`（新）、`src/types/role.ts`（新）、`src/types/user.ts`（去硬编码 UserRole）。
