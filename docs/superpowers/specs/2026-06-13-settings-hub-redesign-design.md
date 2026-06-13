# 设置中心（管理控制台）重设计 设计文档

> 日期：2026-06-13
> 分支：`feat/neutral-slate-ui-redesign`
> 关联：上一轮「中性灰阶 UI 重设计」+「数据页交互重设计」的延续，复用同一套密度令牌与瘦身风格。

## 背景与目标

当前管理员功能分散在两个顶级侧边栏菜单下，结构臃肿、密度低：

- **数据工具**（6 项平铺）：导出脚本 · 校验脚本 · ETL 管理 · 数据查询 · 数据导出 · AI 定时任务
- **系统配置**（三级嵌套，10 个叶子）：
  - 平台管理（8）：菜单管理 · 页面配置 · 用户管理 · Open API · AI 配置 · Webhook · 系统设置 · 角色权限
  - 系统运维（2）：操作日志 · 系统备份

合计 **16 个叶子入口**，系统配置深达 **3 级**，且每个叶子是一个独立整页路由，很多页面只承载一小块配置 → 功能密度低、导航冗长、两个顶级菜单语义重叠（"工具" vs "配置" 边界模糊；AI 配置在系统配置、AI 定时任务却在数据工具）。

**目标**：把两个菜单合并为单一「设置中心」master-detail 外壳，左侧 7 类领域导航、右侧内容区，高相关功能用页内 tab 合并；统一紧凑页面骨架，最大化功能密度。**保留全部现有功能与权限语义，零功能删除，不改后端，不重写现有管理页内部逻辑**。

**已确认的取向（brainstorming 决议）**：
1. 顶层结构 = 合并为单一「设置中心」。
2. 左侧分类 = 按领域细分 7 类。

## 现状锚点（实现前必读）

**16 个管理页组件**（`src/views/admin/`，均为现有、本次复用不重写）：
`MenuManager.vue` · `PageConfigManager.vue` · `UserManager.vue` · `RoleManager.vue` · `ApiKeyManager.vue` · `AiSettings.vue` · `WebhookSettings.vue` · `SystemSettings.vue`（平台管理/通用） · `OperationLog.vue` · `BackupManager.vue`（系统运维） · `ExportScriptManager.vue` · `ValidationScriptManager.vue` · `EtlTaskManager.vue` · `QueryConsole.vue` · `DataMigrationPage.vue`（数据工具） · `AiScanTaskManager.vue`（AI 定时任务）。

**现有路由**（`src/router/index.ts`，`staticRoutes[].children`，当前为 `/` 布局的平级子路由）：`admin/menu`、`admin/page-config`、`admin/users`、`admin/roles`、`admin/api-keys`、`admin/ai-settings`、`admin/webhook-settings`、`admin/system-settings`、`admin/operation-log`、`admin/backup`、`admin/export-scripts`、`admin/validation-scripts`、`admin/etl-tasks`、`admin/query`、`admin/menu-export`（→ DataMigrationPage，菜单名"数据导出"）、`admin/ai-scan-tasks`。另有隐藏页 `admin/factory-reset`（不在菜单，保持现状）。

**菜单数据（DB 驱动）**：种子在 `server/seed_data.py` 的 `MENUS`（`menu-3-b` 数据工具 / `menu-3` 系统配置 及其子项）。运行库可能已被自定义（实测数据工具含 6 项、系统配置含平台管理+系统运维）。菜单树前端经 `src/stores/menu.ts` + `src/api/menu.ts`（后者已有"过滤掉系统配置、数据工具等静态页面菜单"逻辑，见 `menu.ts:87`）渲染于 `SideMenu.vue` → `MenuItem.vue`。

**权限**：菜单可见性 = `menus.roles`；功能访问 = 后端 `@require_permission('admin.*')`；前端 `auth` store 的 `isSuperuser`/`can()`。这些**不变**。

## 设计

### 架构总览

```
侧边栏（DB 菜单）
└─ 设置中心  →  /admin   （单一入口，替代原"数据工具"+"系统配置"两棵树）

/admin  (SettingsHub.vue 外壳：左侧分类栏 + 右侧 <router-view>)
├─ /admin/access      访问控制   tabs: 用户管理 | 角色权限
├─ /admin/structure   结构配置   tabs: 菜单管理 | 页面配置
├─ /admin/integration 集成对接   tabs: Open API | Webhook
├─ /admin/ai          AI 能力    tabs: AI 配置 | AI 定时任务
├─ /admin/data-ops    数据运维   tabs: 数据查询 | 数据导出 | ETL | 导出脚本 | 校验脚本
├─ /admin/sys-ops     系统运维   tabs: 操作日志 | 系统备份
└─ /admin/general     通用设置   系统设置（单页，无 tab）
```

### 组件结构（新建 vs 复用）

- **新建** `src/views/admin/SettingsHub.vue`：master-detail 外壳。左侧 7 项分类导航（持久），右侧 `<router-view>`。提供统一标题/面包屑/内边距骨架。左栏分类按权限过滤：某分类下所有 tab 都无权限 → 该分类项隐藏。
- **新建** 7 个轻量分类容器页（`src/views/admin/hub/` 下），每个用 Element Plus 轻量下划线 `el-tabs`，每个 tab 直接 `<KeepAlive>` 挂载对应**现有**管理组件：
  - `AccessCenter.vue` → tabs 挂 `UserManager` / `RoleManager`
  - `StructureCenter.vue` → `MenuManager` / `PageConfigManager`
  - `IntegrationCenter.vue` → `ApiKeyManager` / `WebhookSettings`
  - `AiCenter.vue` → `AiSettings` / `AiScanTaskManager`
  - `DataOpsCenter.vue` → `QueryConsole` / `DataMigrationPage` / `EtlTaskManager` / `ExportScriptManager` / `ValidationScriptManager`
  - `SysOpsCenter.vue` → `OperationLog` / `BackupManager`
  - `general` 直接渲染 `SystemSettings`（无需容器）
  - 每个容器内的 tab 同样按 capability 过滤：无权限 tab 不渲染；当前 tab 由 query 参数 `?tab=` 记忆（深链友好）。

> **复用即不重写**：现有 16 个组件内部逻辑、API、props 一律不动；它们只是从"独立整页"变成"在 tab 内渲染"。

### 路由改造（`src/router/index.ts`）

- 新增父路由 `{ path: 'admin', component: SettingsHub, redirect: '/admin/access', children: [...7 子路由...] }`。
- 7 个子路由：`access`/`structure`/`integration`/`ai`/`data-ops`/`sys-ops`/`general`，分别指向对应容器页（`general` 指向 `SystemSettings`）。每个挂 `meta.title` 与所需 `admin.*` 权限提示。
- **旧路径兼容**：保留 16 个旧 `admin/*` 路径为 `redirect` 到「新分类路由 + `?tab=`」。例：`admin/users` → `/admin/access?tab=users`；`admin/menu` → `/admin/structure?tab=menu`；`admin/query` → `/admin/data-ops?tab=query`；等全表见实现计划。`admin/factory-reset` 保持原样（隐藏页，不并入）。
- 路由守卫 `hasRoutePermission` 逻辑沿用；新子路由的权限沿用各自原页的权限点。

### 菜单数据改造（DB 种子 + 运行库）

- `server/seed_data.py` 的 `MENUS`：删除 `menu-3-b`（数据工具）整棵、`menu-3`（系统配置）整棵及其所有子项；新增单项 `{"id": "menu-settings", "name": "设置中心", "icon": "Setting", "pageId": None, "parentId": None, "path": "/admin", "roles": ["admin"], "order": ...}`。
- 提供一个**幂等迁移脚本** `server/migrations/2026-06-13_settings_hub_menu.py`（或在 `init_db.py` 风格的一次性脚本里）：在已有运行库中，删除原数据工具/系统配置菜单子树、插入「设置中心」菜单项。脚本可重复执行（先判存在）。
- `src/api/menu.ts` 现有"过滤静态页面菜单"逻辑需复核：确保「设置中心」作为静态 hub 入口能正确出现在侧边栏（按需调整其静态菜单白名单）。

### 页面样式 / 密度

- **外壳骨架统一**：`SettingsHub` 顶部统一标题条（当前分类名 + 面包屑「设置中心 / 分类」），右侧内容区统一内边距；移除/弱化各管理页自带的重复大标题与外层 `el-card` padding（仅在确有重复时收口；优先用容器层覆盖样式，尽量不改子组件模板）。
- **左栏**：~200px，发丝分隔线，激活态主色左缘条 + 浅色底（复用 `--app-shell-active-bg`），与 `SideMenu` 风格一致；分类项可带图标。
- **tab**：轻量下划线式（复用上一轮「标签栏瘦身」`ContentArea.vue` 的 underline 风格），不占额外竖向空间。
- **内容**：表格/表单沿用紧凑模式密度令牌（`--app-table-row-py` 等），信息密度最大化。
- 暗色/紧凑模式跟随全局 `html.dark` / `html.compact-mode`。

### 权限保持（RBAC）

- 左栏分类可见性：分类下存在至少一个当前角色可见的 tab 才显示该分类（用 `auth` store 的 `can('admin.x')` / `isSuperuser` 判定，逐 tab 映射到其原权限点）。
- tab 可见性：无权限 tab 不渲染（与数据页「操作菜单」分组隐藏同款模式）。
- 后端权限点与 `menus.roles` 不变；纯前端重组 + 一处菜单数据迁移。

### 不在范围（YAGNI）

- 不改 16 个管理页的内部逻辑、API、字段。
- 不新增"全局设置搜索"等未确认能力（可作后续）。
- 不动「联动规则 / 依赖管理 / 数据迁移(menu-export 作为 tab 并入数据运维)」之外的、仅靠上下文入口访问的功能；`trigger-rules`/`dependency-manager` 当前不在这两个菜单内，保持现状（其路由仍可经数据页等上下文入口访问）。
- 不改后端业务逻辑（仅菜单种子 + 一次性迁移脚本）。

## 测试策略

- **前端单测**：
  - `SettingsHub` 左栏按权限渲染分类（superuser 见 7 类；受限角色仅见有权限的分类）。
  - 各容器页 tab 按权限过滤、`?tab=` 记忆当前 tab、默认 tab 正确。
  - 路由重定向：旧 `admin/*` 路径正确 301 到新「分类 + ?tab」。
- **既有测试**：`src/router/__tests__/dynamicRoutes.test.ts` 等保持通过；现有 16 个管理页若有单测，因组件未改应继续通过。
- **后端**：菜单种子/迁移脚本幂等性测试（可在 `server/tests/` 加一例，断言迁移后存在「设置中心」、不存在旧两棵树）。
- **视觉验证**：Playwright 截图核对——侧边栏顶级只剩「设置中心」；进入后左栏 7 类、右侧 tab 切换、深链 `?tab=` 生效、紧凑骨架、暗色正常。

## 净效果

- 侧边栏顶级：2 棵树（16 叶、最深 3 级）→ **1 个「设置中心」入口**。
- 内部：**7 类**领域导航 + 页内 tab，切换零整页跳转。
- 16 个独立整页 → 复用进 7 个分类容器，统一紧凑骨架，密度显著提升。
- **零功能删除，零后端逻辑改动，权限语义不变。**
