/**
 * 设置中心目录 —— 唯一真源。
 *
 * 侧边栏渲染、路由生成、权限判定全部从这里派生：新增一个设置功能只需在
 * 这里加一条，路由和权限门会自动跟上，不会出现「加了功能忘了加路由」。
 *
 * 分组（SettingsGroup）只是**视觉分组**：不可点、不折叠、不参与路由。
 */
import type { Component } from 'vue'

export interface SettingsItem {
  /** 路由末段，如 'users' → /admin/users */
  id: string
  label: string
  /** 所需管理能力 key（admin.*） */
  perm: string
  /** 懒加载的功能组件 */
  component: () => Promise<Component>
  /** 不可逆/高危操作：视觉区分，且排在所在组最末 */
  danger?: boolean
}

export interface SettingsGroup {
  id: string
  /** 侧边栏分组标题 */
  label: string
  /** Element Plus 图标组件名 */
  icon: string
  items: SettingsItem[]
}

export const SETTINGS_GROUPS: readonly SettingsGroup[] = [
  { id: 'access', label: '访问控制', icon: 'Lock', items: [
    { id: 'users', label: '用户管理', perm: 'admin.users',
      component: () => import('@/views/admin/UserManager.vue') },
    { id: 'roles', label: '角色权限', perm: 'admin.roles',
      component: () => import('@/views/admin/RoleManager.vue') },
  ] },
  { id: 'structure', label: '结构配置', icon: 'Files', items: [
    { id: 'menu', label: '菜单管理', perm: 'admin.menus',
      component: () => import('@/views/admin/MenuManager.vue') },
    { id: 'page-config', label: '页面配置', perm: 'admin.page_configs',
      component: () => import('@/views/admin/PageConfigManager.vue') },
    { id: 'workflows', label: '工作流', perm: 'admin.workflows',
      component: () => import('@/views/admin/WorkflowManager.vue') },
    { id: 'dependency-manager', label: '依赖管理', perm: 'admin.dependencies',
      component: () => import('@/views/admin/DependencyManager.vue') },
  ] },
  { id: 'integration', label: '集成对接', icon: 'Link', items: [
    { id: 'api-keys', label: 'Open API', perm: 'admin.api_keys',
      component: () => import('@/views/admin/ApiKeyManager.vue') },
    { id: 'webhook', label: 'Webhook', perm: 'admin.webhooks',
      component: () => import('@/views/admin/WebhookSettings.vue') },
    { id: 'kefu', label: '智能客服', perm: 'admin.kefu',
      component: () => import('@/views/admin/KefuManager.vue') },
  ] },
  { id: 'ai', label: 'AI 能力', icon: 'MagicStick', items: [
    { id: 'ai-settings', label: 'AI 配置', perm: 'admin.ai_settings',
      component: () => import('@/views/admin/AiSettings.vue') },
    { id: 'ai-scan', label: 'AI 定时任务', perm: 'admin.ai_scan',
      component: () => import('@/views/admin/AiScanTaskManager.vue') },
    { id: 'ai-batches', label: 'AI 批任务', perm: 'admin.ai_chat_admin',
      component: () => import('@/views/admin/AiBatchAdmin.vue') },
    { id: 'ai-sessions', label: 'AI 会话管理', perm: 'admin.ai_chat_admin',
      component: () => import('@/views/admin/AiSessionAdmin.vue') },
  ] },
  { id: 'data-ops', label: '数据运维', icon: 'DataLine', items: [
    { id: 'query', label: '数据查询', perm: 'admin.query',
      component: () => import('@/views/admin/QueryConsole.vue') },
    { id: 'data-export', label: '数据导出', perm: 'admin.menus',
      component: () => import('@/views/admin/DataMigrationPage.vue') },
    { id: 'etl', label: 'ETL 管理', perm: 'admin.etl_tasks',
      component: () => import('@/views/admin/EtlTaskManager.vue') },
    { id: 'export-scripts', label: '导出脚本', perm: 'admin.export_scripts',
      component: () => import('@/views/admin/ExportScriptManager.vue') },
    { id: 'validation-scripts', label: '校验脚本', perm: 'admin.validation_scripts',
      component: () => import('@/views/admin/ValidationScriptManager.vue') },
    { id: 'trigger-rules', label: '联动规则', perm: 'admin.trigger_rules',
      component: () => import('@/views/admin/TriggerRuleManager.vue') },
  ] },
  { id: 'sys-ops', label: '系统运维', icon: 'Monitor', items: [
    { id: 'operation-log', label: '操作日志', perm: 'admin.operation_logs',
      component: () => import('@/views/admin/OperationLog.vue') },
    { id: 'backup', label: '系统备份', perm: 'admin.backup',
      component: () => import('@/views/admin/BackupManager.vue') },
    { id: 'factory-reset', label: '恢复出厂设置', perm: 'admin.backup', danger: true,
      component: () => import('@/views/admin/FactoryReset.vue') },
  ] },
  { id: 'general', label: '通用设置', icon: 'Setting', items: [
    { id: 'system-settings', label: '系统设置', perm: 'admin.system_config',
      component: () => import('@/views/admin/SystemSettings.vue') },
  ] },
]

/** 扁平化的全部条目，供路由生成与权限查表 */
export const ALL_SETTINGS_ITEMS: readonly SettingsItem[] =
  SETTINGS_GROUPS.flatMap(g => g.items)

/** 按条目 id 查找（路径末段 → 条目） */
export function findSettingsItem(id: string): SettingsItem | undefined {
  return ALL_SETTINGS_ITEMS.find(i => i.id === id)
}

/** 按权限过滤：剔除无权限条目，再剔除空组 */
export function filterGroups(can: (key: string) => boolean): SettingsGroup[] {
  return SETTINGS_GROUPS
    .map(g => ({ ...g, items: g.items.filter(i => can(i.perm)) }))
    .filter(g => g.items.length > 0)
}

/**
 * 用户可访问的首个条目路径；一个都没有时回退 /home。
 * 供 /admin 的动态 redirect 使用。
 */
export function firstAccessibleItemPath(can: (key: string) => boolean): string {
  const groups = filterGroups(can)
  return groups.length ? `/admin/${groups[0].items[0].id}` : '/home'
}

/**
 * 老路径别名 → 新条目 id。
 *
 * 拍平后大多数老路径（/admin/users 等）与条目 id 同名、自动变成真路由，
 * 但这 4 条历史命名与新 id 不同，删掉重定向会直接 404，必须保留。
 */
export const LEGACY_PATH_ALIASES: Record<string, string> = {
  'webhook-settings': 'webhook',
  'ai-scan-tasks': 'ai-scan',
  'menu-export': 'data-export',
  'etl-tasks': 'etl',
}
