# 自定义角色与权限系统（RBAC）使用与设计文档

> 适用版本：`feat/custom-roles-rbac` 分支起。配套规格/计划见
> `docs/superpowers/specs/2026-06-02-custom-roles-rbac-design.md`、
> `docs/superpowers/plans/2026-06-02-custom-roles-rbac.md`。

## 1. 功能概述

系统原本只有 3 个**硬编码**角色（管理员 / 开发人员 / 访客）。现在角色**可自定义**，
并能在三个粒度上控制每个功能的权限：

| 粒度 | 控制什么 | 在哪配置 |
|------|----------|----------|
| **管理功能开关** | 每个 `/admin` 管理模块（用户管理、备份、Webhook、ETL……）是否可用 | 角色权限页 → 「管理功能」 |
| **数据页 CRUD** | 对每个数据页分别控制 读 / 增 / 改 / 删 | 角色权限页 → 「数据页权限」 |
| **菜单可见性** | 角色能在侧边栏看到哪些菜单 | 菜单管理页（每个菜单的「角色」字段） |

> **文件/图片字段上传**遵循同一套「数据页 CRUD」权限：只要角色对该数据页有**增或改**权限即可上传附件（包括被授予写权限的访客 / 自定义角色），无需额外的全局写权限。

**核心特性**

- **内置角色**：`admin` 是**永久超级管理员**（拥有全部权限、不可删除、不可降权）；
  `developer` / `guest` 是可编辑的预置角色。可新增任意自定义角色。
- **服务端权威**：所有权限在后端强制校验（返回 403），前端的按钮隐藏/菜单过滤只是 UX。
- **即时生效**：JWT 只携带角色 slug；后端每次请求从内存缓存解析权限，**编辑角色后无需用户重新登录**
  （前端在下次刷新/进入应用时通过 `/auth/me` 拉到最新权限）。

## 2. 权限模型

### 2.1 内置角色（种子）

| 角色 | 超管 | 默认数据页权限 | 管理功能 |
|------|------|----------------|----------|
| `admin` | 是 | 读写 | 全部（超管短路） |
| `developer` | 否 | 读写 | 无 |
| `guest` | 否 | 只读 | 无 |

### 2.2 默认数据页权限（`defaultPageAccess`）

每个角色有一个「未配置数据页默认」值，作用于**没有单独配置**的数据页：

- `none`（无）：看不到、读不了。
- `read`（只读）：能读，不能写。**新建角色的默认值。**
- `write`（读写）：增删改读全开。

> 在「数据页权限」表里**单独勾选**过的数据页，按勾选结果计算；未勾选的页走上面的默认值。
> 保存时只持久化「至少勾选了一项」的行（其余依赖默认值）。

### 2.3 管理功能能力目录

约 22 个能力 key（`admin.users`、`admin.backup`、`admin.webhooks`……），由
`server/utils/permissions.py` 的 `PERMISSION_CATALOG` 统一定义，角色权限页据此渲染开关。
**默认拒绝**：只有超管 + 被显式勾选的角色可用。

## 3. 使用指南

### 3.1 进入角色权限页

以管理员登录 → 侧边栏「系统配置 → 平台管理 → 角色权限」（路径 `/admin/roles`）。

### 3.2 新建 / 编辑角色

1. 点「新建角色」，填名称、描述、默认数据页权限。
2. 在右侧编辑器：
   - **「管理功能」标签**：勾选该角色可用的管理模块。
   - **「数据页权限」标签**：设默认值，并对需要细控的数据页逐项勾选 读/增/改/删。
3. 点「保存」。**超级管理员**角色全部显示为勾选且禁用，不可修改。
4. 删除：内置角色不可删；自定义角色若仍被用户使用会被拒绝（先重新分配用户）。
   删除时会自动从所有菜单的「角色」白名单中移除该角色 slug。

### 3.3 给用户分配角色

「用户管理」页新增/编辑用户时，「角色」下拉会列出所有角色（含自定义）。

### 3.4 ⚠️ 菜单可见性与能力是两套独立机制

这是最容易困惑的点：

- **能力**（`admin.*` / 数据页 CRUD）决定**能不能访问/操作**（后端 403 强制）。
- **菜单可见性**（`menus.roles` 白名单）决定**侧边栏能不能看到入口**。

二者**互不替代**。给自定义角色授予了某能力后，如果还想让用户从侧边栏点进去，
需要到**菜单管理**页，把该角色 slug 加进对应菜单的「角色」字段。否则该用户即便
有权限，也只能通过直达 URL 进入（路由守卫对 `/admin/*` 按能力放行）。

> 经验做法：为一类岗位新建角色后，既在「角色权限」里配能力，也在「菜单管理」里把
> 该角色加进它该看到的菜单。

## 4. 设计与实现

### 4.1 数据模型（PostgreSQL）

```
roles(id, name, description, is_system, is_superuser, default_page_access)
role_permissions(role_id, permission_key)                 -- 管理功能授权，有行=授权
role_page_permissions(role_id, page_id, can_read, can_create, can_update, can_delete)
menus.roles  (JSONB 角色 slug 数组)                        -- 菜单可见性，复用既有字段
users.role   (text，应用层校验存在于 roles 表，已去掉旧的 CHECK 约束)
```

### 4.2 后端强制

- `server/utils/permissions.py`：单一事实来源。
  - `PERMISSION_CATALOG` + `catalog_keys()`
  - `get_role_perms(role_id)`：从 3 张表解析角色权限，**进程内缓存**（线程锁，
    超管走短路返回空集合），`invalidate_cache()` 在角色写操作后失效。
  - `can_admin(role, key)` / `can_page(role, page_id, action)`：超管恒真；否则查授权/默认值。
- `server/auth.py` 的 `@require_permission('admin.x')` 取代旧的 `@admin_required`
  （约 19 个路由文件、105 处装饰器迁移完成）。
- `server/utils/rbac_guard.py` 的 `require_page_action(collection, action)` 供
  `routes/dynamic.py`（数据 CRUD）与 `routes/relations.py`（关系写=父集合 update）复用。
- `routes/auth.py`：登录 / `/auth/me` 返回 `permissions`（`isSuperuser`、`adminKeys`、
  `defaultPageAccess`、`pagePerms`）与 `roleName`。

### 4.3 前端门禁（仅 UX，后端权威）

- `src/stores/auth.ts`：
  - `isSuperuser`（= `permissions.isSuperuser`，缺失时回退 `role==='admin'`）。
  - `can(key)` / `canPage(pageId, action)`：均以 `isSuperuser` 为优先短路。
  - `hasRoutePermission(path)`：`/admin/*` 按 `ADMIN_PATH_PERMISSION` 映射到能力 key 判定；
    数据/菜单路径按 `menus.roles` 白名单（超管恒放行）。
- `src/router/index.ts`：应用首次初始化时调用 `fetchCurrentUser()` 刷新权限。
- `src/stores/menu.ts` + `SideMenu.vue`：超管显示全部菜单；否则按 `menus.roles` 过滤（支持自定义 slug）。
- `src/views/admin/RoleManager.vue`：角色管理 UI；`src/stores/role.ts` + `src/api/role.ts`。
- `src/views/dynamic/DynamicPage.vue` + `src/components/common/DataTable.vue`：
  新增/编辑/删除按钮按 `canPage` 显隐。

### 4.4 REST 接口

```
GET    /roles            列表
GET    /roles/:id        详情（含 adminKeys / pagePermissions）
GET    /roles/catalog    能力目录
POST   /roles            新建
PUT    /roles/:id        更新（替换 adminKeys / pagePermissions / 默认值；超管跳过）
DELETE /roles/:id        删除（内置/在用拒绝；清理 menus.roles）
```
均受 `@require_permission('admin.roles')` 保护，蓝图在 `dynamic_bp`（catch-all）之前注册。

## 5. 注意事项与已知点

- **管理员永不被锁死**：即使会话里 `permissions` 缺失（旧会话/未刷新），`role==='admin'`
  也会被当作超管放行；同时应用初始化会刷新一次 `/auth/me`。（本项为修复
  "admin 进不去系统管理" 的关键改动。）
- **能力 ≠ 菜单可见性**：见 3.4。自定义角色要既配能力、又配菜单可见性。
- **`admin.query` / `admin.comments`** 等少数能力目前后端无对应 `@admin_required`
  路由（这些模块用 `login_required`/`write_required`），它们主要用于前端路由/菜单映射。
- **范围之外（YAGNI）**：字段级权限、角色继承、行级（per-record）权限、限时授权。

## 6. 测试与验证

- 后端：`npm run test:server`（含 `test_permissions`、`test_routes_roles`、
  `test_dynamic_page_permissions`、`test_route_permission_keys` 等）。
- 前端：`npm run test`（含 `permissions.test.ts`）。
- 端到端（Playwright 已手动验证）：超管全功能、自定义角色创建/配置/落库、用户分配、
  `/admin/*` 按能力放行/拒绝、数据页 CRUD 后端 403、按钮显隐、菜单可见性、
  旧会话不锁死管理员、自定义角色显示名。
