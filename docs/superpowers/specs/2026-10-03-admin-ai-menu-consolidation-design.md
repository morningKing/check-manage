# 后台 AI 能力菜单合并精简 设计

> 日期：2026-10-03 ｜ 状态：已评审（用户确认 §1–§5 设计与机制 A）
> 背景：设置中心「AI 能力」组平铺 8 个条目，为全设置中心最大组，管理动线割裂。目标：按域合并为 4 个入口，页内 tab 切换。

## 1. 菜单结构（8 → 4）

`src/views/admin/hub/settingsCatalog.ts` 是菜单/路由/权限的唯一真源，本次只改这一处目录 + 新增 3 个壳组件，7 个现有视图组件零改动。

| 新条目（id） | 页内 tab（权限保持原有 key） | 条目可见性（any-of） |
|--------------|------------------------------|----------------------|
| `ai-settings` AI 配置（**复用旧 id**） | 模型与密钥（`admin.ai_settings`）→ `AiSettings.vue`；运行时（`admin.ai_runtime_read`）→ `AiOpencodeRuntime.vue` | `ai_settings` 或 `ai_runtime_read` |
| `ai-skills` AI 技能（**复用旧 id**） | 技能广场（`admin.ai_settings`）→ `AiSkillManager.vue`；拟合优化（`admin.ai_chat_admin`）→ `AiSkillOpt.vue` | `ai_settings` 或 `ai_chat_admin` |
| `ai-execution` AI 执行中心（新 id） | 批量执行（`admin.ai_chat_admin`）→ `AiBatchAdmin.vue`；会话审计（`admin.ai_chat_admin`）→ `AiSessionAdmin.vue`；编排管理（`admin.ai_orchestration_admin`）→ `AiOrchestrationManager.vue` | `ai_chat_admin` 或 `ai_orchestration_admin` |
| `ai-scan` AI 定时巡检（不变） | — | `admin.ai_scan` |

默认 tab（无 `?tab=` 或非法值时落地）：AI 配置 → `model`（模型与密钥）；AI 技能 → `square`（技能广场）；AI 执行中心 → `batches`（批量执行）。全部 tab id 可寻址：`ai-settings` = `model`/`runtime`，`ai-skills` = `square`/`fit`，`ai-execution` = `batches`/`sessions`/`orchestrations`。

## 2. 旧路径重定向（5 条）

复用 `ai-settings` / `ai-skills` 作为壳路由根路径，故仅 5 条重定向（`settingsCatalog.ts` 内新增 `SETTINGS_REDIRECTS` 常量，路由生成器消费）：

| 旧路径 | 重定向到 |
|--------|----------|
| `/admin/ai-opencode` | `/admin/ai-settings?tab=runtime` |
| `/admin/ai-skillopt` | `/admin/ai-skills?tab=fit` |
| `/admin/ai-batches` | `/admin/ai-execution?tab=batches` |
| `/admin/ai-sessions` | `/admin/ai-execution?tab=sessions` |
| `/admin/ai-orchestrations` | `/admin/ai-execution?tab=orchestrations` |

书签、使用文档（编排使用指导中 `/admin/ai-orchestrations`）、e2e 引用（TC-BATCH-015、`ai-governance-audit` spec）全部不断链。

## 3. 目录 schema 扩展

```ts
export interface SettingsItem {
  id: string
  label: string
  /** 任一权限即显示条目（string 为单权限兼容旧形态） */
  perm: string | string[]
  component: () => Promise<Component>
  icon: string
  danger?: boolean
  /** 页内 tab：id/label/perm/组件；无 tab 的条目不声明（ai-scan 等不变） */
  tabs?: SettingsTab[]
}
export interface SettingsTab {
  id: string          // ?tab= 的值，如 'runtime' / 'fit' / 'batches'
  label: string
  perm: string        // tab 级细粒度权限，无权限不渲染该 tab
  component: () => Promise<Component>
}
```

- `ALL_SETTINGS_ITEMS` / `findSettingsItem` 语义不变；`filterGroups` 改为 any-of 判定（`[].some`）。
- 路由生成器与路由守卫（settingsHubRoutes 派生逻辑）同步支持 any-of。
- 重定向由路由生成器把 `SETTINGS_REDIRECTS` 展开为 `redirect: { path, query }`。

## 4. 壳组件

新增 `src/views/admin/hub/`：`AiConfigHub.vue`、`AiSkillHub.vue`、`AiExecutionHub.vue`。每个壳组件职责单一：

1. 从 catalog 读自身条目定义（不硬编码 tab 清单）；
2. `el-tabs` 渲染有权看到的 tab，懒加载挂载对应视图（`defineAsyncComponent` + `keep-alive` 保持切换状态）；
3. `tab` query 深链：进页读 `route.query.tab` 定位（非法值回退默认 tab），切换时 `router.replace` 回写，保证可收藏、可分享；
4. 不含任何业务逻辑。

## 5. 兼容性与测试

- **vitest**：`settingsHubRoutes.test.ts` 适配 any-of 权限与重定向表断言；新增壳组件测试（tab 权限过滤、query 读写、非法 tab 回退、懒挂载）。
- **e2e**：新增 1 条导航冒烟（4 入口可达 + 5 条旧路径重定向落对 tab）；现有 43 例全量回归（`/admin/ai-batches` 引用经重定向应继续通过）。
- **文档**：`docs/user-guide/` 编排指导等涉及旧路径的措辞顺手更新；菜单截图更新。

## 6. 不做（YAGNI）

- 不做子路由真路径（`/admin/ai-execution/batches`）——query tab 够用，避免打破 catalog 单路由派生模型。
- 不合并任何视图组件内容、不改任何后端端点与权限 key。
- 不做 tab 记忆（localStorage）——query 已承担状态承载。
