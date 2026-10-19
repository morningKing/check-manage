# 设置中心扁平化为独立外壳 设计

日期：2026-08-10
状态：已确认，待编写实施计划

## 1. 背景与目标

### 现状

设置中心今天是四层嵌套：

```
AppLayout 业务侧边栏（工作空间→项目→数据页，含一条「设置中心」入口）
  └─ SettingsHub.vue 的第二条侧边栏（7 个分类）
       └─ CategoryView.vue 的 tab 条（data-ops 下有 5 个）
            └─ 实际功能组件（UserManager / BackupManager / …）
```

- 入口来自 `menus` 表的 `menu-settings` 行（`path=/admin`、`menu_type='system'`），由 `SideMenu` 从菜单树渲染。
- `/admin` → `SettingsHub.vue`，其 7 个子路由全部指向同一个 `CategoryView.vue`，靠 `meta.categoryId` 区分。
- `CategoryView.vue` 读 `?tab=` 参数，从 `settingsComponents.ts` 的 id→组件映射里取出功能组件，包在 `KeepAlive :max="8"` 里渲染。
- 路由表里有 15 条 `/admin/<旧路径>` → `/admin/<分类>?tab=<x>` 的兼容重定向。

18 个功能分散在 7 个分类之下；另有 3 个管理页游离在设置中心之外，各自单挂路由。

### 用户反馈的核心痛点

**点太多次才能到一个功能。** 想改个角色权限要「点设置中心 → 选访问控制 → 切 tab」，常用功能没有直达入口，18 个功能埋在 7 个分类后面找不到。

（并排两条侧边栏浪费屏幕、心智模型混乱也是问题，但不是主要矛盾。）

### 目标

1. **一次点击到达任何功能**：侧边栏平铺全部功能，分类降级为纯视觉分组。
2. **每个功能有自己的 URL**：`/admin/<功能>`，可收藏、可分享。
3. **设置侧边栏取代业务侧边栏**：进设置中心后屏幕上只有一条导航，心智模型清晰（要么在干活、要么在配置）。
4. **游离在外的管理页一并收编**：设置中心成为「全部设置的唯一入口」。

### 非目标

- 不做设置首页卡片墙。
- 不做搜索 / 命令面板。
- 不改任何功能组件的内部逻辑（`UserManager.vue` 等一律原样复用）。
- 不改后端。

## 2. 方案选型

**采纳方案 A：由目录数据生成子路由，每个功能一个真实路由。**

已否决：

- **方案 B（单个动态容器路由 `/admin/:section`）**：路由表最短，但权限强制得留在组件里（守卫拿不到静态 `meta`），面包屑标题也要回查目录 —— 把「一处生成」换成「多处查表」，没省多少还散了。
- **方案 C（最小改动，只改导航渲染）**：保留 `CategoryView` 与 `?tab=`，仅把侧边栏改成平铺。改动量最小，但 URL 仍是 `/admin/data-ops?tab=etl` 形状，且「分类」这层在数据结构里还在、只是 UI 上藏了，后续维护者会困惑。

选 A 的理由：用户确认的三件事（平铺、取代业务侧边栏、沿用 `/admin/<功能>`）合起来本质上是「取消分类这一层的**路由**意义，只保留它的**视觉分组**意义」。A 是唯一真正做到这点的，且净删代码。

## 3. 目录数据模型与路由生成

### 3.1 目录改形

`src/views/admin/hub/settingsCatalog.ts` 重写：从「分类 + tab」变成「分组 + 条目」。分组降级为纯展示属性 —— 不可点、不折叠、不参与路由。

```ts
export interface SettingsItem {
  /** 路由末段，如 'users' */
  id: string
  label: string
  /** 权限键，如 'admin.users' */
  perm: string
  /** 懒加载的功能组件 */
  component: () => Promise<Component>
  /** 不可逆/高危操作，视觉区分并排在最末 */
  danger?: boolean
}

export interface SettingsGroup {
  id: string
  /** 侧边栏分组标题 */
  label: string
  icon: string
  items: SettingsItem[]
}

export const SETTINGS_GROUPS: SettingsGroup[] = [ /* 7 组，共 21 条 */ ]
```

### 3.2 21 条条目

原有 18 条（沿用现有 `SETTINGS_CATALOG` 的 id 与 `perm`）：

| 分组 | 条目 id |
|---|---|
| 访问控制 | `users`、`roles` |
| 结构配置 | `menu`、`page-config`、`workflows` |
| 集成对接 | `api-keys`、`webhook`、`kefu` |
| AI 能力 | `ai-settings`、`ai-scan` |
| 数据运维 | `query`、`data-export`、`etl`、`export-scripts`、`validation-scripts` |
| 系统运维 | `operation-log`、`backup` |
| 通用设置 | `system-settings` |

新收编 3 条（今天各自单挂路由）：

| 条目 id | 组件 | 权限键 | 归入分组 | 备注 |
|---|---|---|---|---|
| `trigger-rules` | `TriggerRuleManager.vue` | `admin.trigger_rules` | 数据运维 | 前端今天**无权限门** |
| `dependency-manager` | `DependencyManager.vue` | `admin.dependencies` | 结构配置 | 前端今天**无权限门** |
| `factory-reset` | `FactoryReset.vue` | `admin.backup` | 系统运维 | `danger: true`，今天 `hidden` 不在任何菜单里 |

`admin.trigger_rules` / `admin.dependencies` 两个权限键在 `server/utils/permissions.py` 的 `PERMISSION_CATALOG` 里已存在，后端 `@require_permission` 也已在强制。收编顺带补上前端的门 —— 这是本次改造的一个附带修复，不是新增能力。

`factory-reset` 的后端权限是 `admin.backup`（`server/routes/backups.py::do_factory_reset`），沿用。

### 3.3 路由生成

路由树再降一层：`/admin` 不再有组件（它原来的侧边栏搬去外壳了），21 条路由直接生成为 **AppLayout 的子路由**，路径写全 `admin/<id>`。

```ts
const settingsChildren = SETTINGS_GROUPS.flatMap(g =>
  g.items.map(it => ({
    path: `admin/${it.id}`,
    name: `Settings_${it.id}`,
    component: it.component,
    meta: { title: it.label, perm: it.perm, shell: 'settings' },
  }))
)
```

另有一条 `/admin` 只做 `redirect` —— 跳到首个有权限的条目。

最终层级：

```
AppLayout（外壳：header + 侧边栏 + 内容区）
  └─ 功能组件（UserManager / BackupManager / …）
```

### 3.4 权限收敛

`src/stores/auth.ts` 的 `hasRoutePermission` 今天对设置中心有一段特判 + 一张 `ADMIN_PATH_PERMISSION` 映射表兜底旧路径。拍平后全部塌缩成一次目录查表：

- `/admin/<id>` → 在目录里找到该条目，读它的 `perm`，交给 `can()`
- `/admin` → 按「是否存在任一有权限的条目」放行

`categoryPerms` / `firstAccessibleCategoryPath` 等为分类模型服务的辅助函数随之删除。

### 3.5 删除的东西

- `src/views/admin/hub/SettingsHub.vue`（外层侧边栏，职责搬到 `SettingsSideMenu.vue`）
- `src/views/admin/hub/CategoryView.vue`（tab 容器）
- `src/views/admin/hub/settingsComponents.ts`（id→组件映射，职责并入目录的 `component` 字段）
- 路由表里 15 条 `/admin/<旧路径>` → `?tab=` 重定向（这些路径变回真路由）
- `settingsCatalog.ts` 里 `resolveActiveTab` / `categoryPerms` / `firstAccessibleCategoryPath` 等 tab 模型的辅助函数
- `auth.ts` 里的 `ADMIN_PATH_PERMISSION` 映射表与 `/admin` 特判分支

### 3.6 行为变化：不再有 KeepAlive

今天 `data-ops` 的 5 个 tab 共用 `KeepAlive :max="8"` 缓存池，同分类内切 tab 时组件状态保留。拍平后每个功能是独立路由，**默认不做 KeepAlive**（离开即销毁）。

这对「查询控制台」这类填了一半表单的场景是行为倒退，已与用户确认接受 —— 简单、可预测优先。

## 4. 页面外壳切换

### 4.1 靠路由 meta 切换

`src/components/layout/AppLayout.vue`：

```vue
<SettingsSideMenu v-if="isSettingsShell" />
<SideMenu v-else />
```

```ts
const isSettingsShell = computed(() => route.meta.shell === 'settings')
```

header、用户信息、主题切换、AI 助手入口、待办入口等全局件一律保留不动。

### 4.2 新组件 `src/components/layout/SettingsSideMenu.vue`

- 顶部一个「← 返回工作区」。
- 下方按 `SETTINGS_GROUPS` 渲染：分组标题是不可点的小标题，条目是 `RouterLink`。
- 整个列表按 `auth.can(item.perm)` 过滤。
- **过滤后为空的分组整组不渲染**（否则会出现光秃秃的标题）。
- `danger` 条目（恢复出厂）排在最末，样式区分。

### 4.3 「返回工作区」回到哪

记住进入设置前的最后一个业务路由，存在 app store 里、由路由守卫更新；没有记录（例如直接刷新落在 `/admin/users`）则回 `/home`。

### 4.4 面包屑

AppLayout header 现有的面包屑对设置路由显示「设置中心 / <功能名>」，`<功能名>` 取自 `meta.title`。

### 4.5 入口不变

`menus` 表的 `menu-settings` 行（`path=/admin`）仍是业务侧边栏里的入口，**不做任何数据迁移**。点它进设置，侧边栏随即切换。

## 5. 迁移与兼容

### 5.1 老书签大部分自动复活，但有 4 条必须保留别名

`/admin/users`、`/admin/roles` 这类今天是重定向到 `?tab=` 形态的；拍平后**路径与条目 id 同名的**变回真路由，重定向表可以删掉，老书签反而更准。

**但有 4 条老路径与新条目 id 不同名**，删掉重定向会直接 404，必须保留为别名重定向：

| 老路径 | 新条目 id / 目标 |
|---|---|
| `/admin/webhook-settings` | `webhook` → `/admin/webhook` |
| `/admin/ai-scan-tasks` | `ai-scan` → `/admin/ai-scan` |
| `/admin/menu-export` | `data-export` → `/admin/data-export` |
| `/admin/etl-tasks` | `etl` → `/admin/etl` |

（`/admin/system-settings` 今天重定向到 `/admin/general`，但条目 id 恰好就是 `system-settings`，拍平后成为真路由，无需别名。）

这 4 条以一张显式的 `LEGACY_PATH_ALIASES` 常量维护，与 §5.2 的分类兼容重定向并列。

### 5.2 新增一层兼容重定向

当前版本用户可能已收藏了 `/admin/access?tab=users` 这种形态。新增（替换掉删掉的 15 条）：

- `/admin/<分类id>` 带 `?tab=<x>` 且 `<x>` 是**目录里存在的条目 id** → 重定向到 `/admin/<x>`
- `/admin/<分类id>` 不带 tab，或 `<x>` 不是合法条目 id → 重定向到该分组下首个**有权限**的条目
- 该分组下一个有权限的条目都没有 → 重定向到 `/admin`（由它再跳到全局首个有权限条目；若一个都没有，走既有的无权限处理）

7 个分类 id：`access` / `structure` / `integration` / `ai` / `data-ops` / `sys-ops` / `general`。分组信息本就在目录里，直接复用，不额外维护映射表。

### 5.3 必须一并改的硬编码

`src/views/workflow/WorkflowEditor.vue:172` 的 `LIST_PATH = '/admin/structure?tab=workflows'` → 改成 `/admin/workflows`。

全仓已 grep 确认组件里只有这一处硬编码设置中心 URL（其余出现均在 `router/index.ts` 自身的重定向表与测试文件里）。

## 6. 测试

### 6.1 要删 / 重写的现有测试

- `src/views/admin/hub/__tests__/CategoryView.test.ts` —— 组件删除，测试一并删
- `src/views/admin/hub/__tests__/SettingsHub.test.ts` —— 同上
- `src/views/admin/hub/__tests__/settingsCatalog.test.ts` —— 数据模型改了，重写
- `src/router/__tests__/settingsHubRoutes.test.ts` —— 路由形态改了，重写

### 6.2 新增测试

- **目录数据**：21 条条目齐全、`id` 唯一、每条都有 `perm`、`danger` 只标在 `factory-reset` 上。
- **路由生成**：生成 21 条；`path` / `name` / `meta.title` / `meta.perm` / `meta.shell` 逐项对上；`/admin` 重定向到首个有权限条目。
- **权限过滤**：admin / developer / guest 三种角色各自可见的条目集合；**空分组不渲染**。
- **兼容重定向**：`/admin/access?tab=roles` → `/admin/roles`；`/admin/data-ops`（无 tab）→ 该分组首个有权限条目。
- **`hasRoutePermission` 收敛后**：`/admin` 与 `/admin/<id>` 各自的放行 / 拒绝。
- **`SettingsSideMenu.vue` 组件测**：分组标题渲染、条目链接、`danger` 排最末、返回按钮存在。

## 7. 验证

### 7.1 Playwright 实跑（CLAUDE.md 强制）

这是纯前端导航改造，更要实跑：

1. admin 登录 → 点侧边栏「设置中心」→ 业务侧边栏被设置侧边栏取代、21 项一屏可见
2. 点任一功能 → 一次点击到位、URL 是 `/admin/<id>`、面包屑显示「设置中心 / <功能名>」
3. 点「← 返回工作区」→ 回到进入设置前的业务页面
4. 直接访问 `/admin/roles` 并刷新 → 正常落地、侧边栏是设置态
5. 老链接 `/admin/access?tab=users` → 重定向到 `/admin/users`
6. **受限角色**（见 7.2）登录 → 无权限条目不出现、整组无权限时组标题也不出现、直接敲无权限 URL 被守卫拦下
7. 工作流编辑器的「返回列表」→ 落到 `/admin/workflows` 而非死链

截图存 `.playwright-mcp/`。

### 7.2 受限角色

库里只有 admin / developer / guest 三个内置角色，后两者对 `admin.*` 基本全无权限，验不到「部分可见、部分不可见」的中间态。

**验证时临时创建一个测试角色**，只开 2-3 个 `admin.*` 权限（建议 `admin.users` + `admin.backup`，跨两个分组，能同时验到「组内部分可见」与「整组不可见」）。验证完删除该角色。

### 7.3 不需要 proxy.py 验证

纯前端路由改造 —— 没有新增 `/api` 路由、没有 SSE、没有新请求头，`proxy.py` 的 `_is_sse()` 与请求头白名单都不受影响。

但 `npm run build` 必须过：路由是生成的，构建期的 tree-shaking 与动态 import 分包值得确认一眼。

## 8. 文档

已 grep 确认需要改的具体位置：

| 文件:行 | 现状 | 改成 |
|---|---|---|
| `docs/user-guide/admin/backups.md:3` | 设置中心 → 系统运维（`/admin/sys-ops`）→ 备份 | 设置中心 → 系统备份（`/admin/backup`） |
| `docs/user-guide/ai/assistant.md:70` | 设置中心 → AI 能力（`/admin/ai`） | 设置中心 → AI 配置（`/admin/ai-settings`） |
| `docs/user-guide/ai/assistant.md:74` | 在 `/admin/ai` 的「外部 MCP 服务」卡片 | 在 `/admin/ai-settings` 的… |
| `docs/user-guide/ai/scan-tasks.md:17` | 「系统配置 → 数据工具 → AI 定时任务」(`/admin/ai-scan-tasks`) | 「设置中心 → AI 定时任务」(`/admin/ai-scan`) —— 注意条目 id 是 `ai-scan` |
| `docs/user-guide/admin/roles-rbac.md:61` | 侧边栏「系统配置 → 平台管理 → 角色权限」（`/admin/roles`） | 路径不变，但导航描述要改成新的一级结构 |
| `docs/user-guide/admin/roles-rbac.md:122` | 描述 `hasRoutePermission` 按 `ADMIN_PATH_PERMISSION` 映射判定 | 改成「按设置中心目录查表判定」——该映射表本次删除 |

⚠️ **不要误改**：`docs/user-guide/ai/smart-customer-service.md` 里的 `/admin/kefu/instances`、`GET /admin/kefu/instances` 等是**后端 API 路径**，与前端路由无关，保持原样。同理 `roles-rbac.md:86` / `:158` 里泛指的 `/admin/*` 表述可保留。

`CLAUDE.md` 目前没有专门描述设置中心结构的小节；若实施时发现有引用，同步更新。

## 9. 涉及文件清单

**新增**

- `src/components/layout/SettingsSideMenu.vue`
- `src/components/layout/__tests__/SettingsSideMenu.test.ts`

**重写**

- `src/views/admin/hub/settingsCatalog.ts`（数据模型改形，含 21 条条目与 `component` 字段）
- `src/views/admin/hub/__tests__/settingsCatalog.test.ts`
- `src/router/__tests__/settingsHubRoutes.test.ts`

**修改**

- `src/router/index.ts`（生成子路由、删 15 条重定向、加分类兼容重定向、删 `/admin` 父组件）
- `src/stores/auth.ts`（`hasRoutePermission` 收敛、删 `ADMIN_PATH_PERMISSION`）
- `src/components/layout/AppLayout.vue`（外壳切换、面包屑）
- `src/stores/app.ts`（记住最后一个业务路由）
- `src/views/workflow/WorkflowEditor.vue`（`LIST_PATH`）
- `docs/user-guide/` 相关文档、`CLAUDE.md`

**删除**

- `src/views/admin/hub/SettingsHub.vue` + `__tests__/SettingsHub.test.ts`
- `src/views/admin/hub/CategoryView.vue` + `__tests__/CategoryView.test.ts`
- `src/views/admin/hub/settingsComponents.ts`

## 10. 实施注意

当前分支 `feat/row-custom-actions` 有一个未合并的 PR（#107）。本改造应从 `main` 另开分支，不要叠在那条线上。
