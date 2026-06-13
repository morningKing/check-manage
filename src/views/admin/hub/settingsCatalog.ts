/**
 * 设置中心目录（纯数据，无 Vue 组件依赖）。
 * 7 个领域分类，每个分类含若干 tab；tab 携带权限 key，用于按角色过滤。
 * 组件挂载映射另见 settingsComponents.ts（避免本模块引入组件，便于 auth store 廉价引用）。
 */
export interface SettingsTabMeta {
  /** tab 标识，同时用于路由 ?tab= 与组件注册表 key */
  id: string
  label: string
  /** 所需管理能力 key（admin.*） */
  perm: string
}

export interface SettingsCategoryMeta {
  /** 分类标识，对应路由 /admin/<id> */
  id: string
  label: string
  /** Element Plus 图标组件名 */
  icon: string
  tabs: SettingsTabMeta[]
}

export const SETTINGS_CATALOG: readonly SettingsCategoryMeta[] = [
  { id: 'access', label: '访问控制', icon: 'Lock', tabs: [
    { id: 'users', label: '用户管理', perm: 'admin.users' },
    { id: 'roles', label: '角色权限', perm: 'admin.roles' },
  ] },
  { id: 'structure', label: '结构配置', icon: 'Files', tabs: [
    { id: 'menu', label: '菜单管理', perm: 'admin.menus' },
    { id: 'page-config', label: '页面配置', perm: 'admin.page_configs' },
  ] },
  { id: 'integration', label: '集成对接', icon: 'Link', tabs: [
    { id: 'api-keys', label: 'Open API', perm: 'admin.api_keys' },
    { id: 'webhook', label: 'Webhook', perm: 'admin.webhooks' },
  ] },
  { id: 'ai', label: 'AI 能力', icon: 'MagicStick', tabs: [
    { id: 'ai-settings', label: 'AI 配置', perm: 'admin.ai_settings' },
    { id: 'ai-scan', label: 'AI 定时任务', perm: 'admin.ai_scan' },
  ] },
  { id: 'data-ops', label: '数据运维', icon: 'DataLine', tabs: [
    { id: 'query', label: '数据查询', perm: 'admin.query' },
    { id: 'data-export', label: '数据导出', perm: 'admin.menus' },
    { id: 'etl', label: 'ETL 管理', perm: 'admin.etl_tasks' },
    { id: 'export-scripts', label: '导出脚本', perm: 'admin.export_scripts' },
    { id: 'validation-scripts', label: '校验脚本', perm: 'admin.validation_scripts' },
  ] },
  { id: 'sys-ops', label: '系统运维', icon: 'Monitor', tabs: [
    { id: 'operation-log', label: '操作日志', perm: 'admin.operation_logs' },
    { id: 'backup', label: '系统备份', perm: 'admin.backup' },
  ] },
  { id: 'general', label: '通用设置', icon: 'Setting', tabs: [
    { id: 'system-settings', label: '系统设置', perm: 'admin.system_config' },
  ] },
]

/** 按权限过滤：剔除无权限 tab，再剔除空分类 */
export function filterCatalog(
  can: (key: string) => boolean
): SettingsCategoryMeta[] {
  return SETTINGS_CATALOG
    .map(c => ({ ...c, tabs: c.tabs.filter(t => can(t.perm)) }))
    .filter(c => c.tabs.length > 0)
}

/** 某分类下全部 tab 的权限 key（分类不存在则空数组） */
export function categoryPerms(categoryId: string): string[] {
  const c = SETTINGS_CATALOG.find(x => x.id === categoryId)
  return c ? c.tabs.map(t => t.perm) : []
}

/** 决定当前激活 tab：query 命中则用之，否则取首个；无 tab 返回 '' */
export function resolveActiveTab(
  tabs: Array<{ id: string }>,
  queryTab: string | undefined
): string {
  if (tabs.length === 0) return ''
  if (queryTab && tabs.some(t => t.id === queryTab)) return queryTab
  return tabs[0].id
}
