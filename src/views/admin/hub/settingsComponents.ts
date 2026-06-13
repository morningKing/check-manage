/**
 * 设置中心 tab id → 现有管理页组件（异步）。
 * 这些组件内部逻辑一律复用、不改。
 */
import { defineAsyncComponent, type Component } from 'vue'

export const SETTINGS_TAB_COMPONENTS: Record<string, Component> = {
  users: defineAsyncComponent(() => import('@/views/admin/UserManager.vue')),
  roles: defineAsyncComponent(() => import('@/views/admin/RoleManager.vue')),
  menu: defineAsyncComponent(() => import('@/views/admin/MenuManager.vue')),
  'page-config': defineAsyncComponent(() => import('@/views/admin/PageConfigManager.vue')),
  'api-keys': defineAsyncComponent(() => import('@/views/admin/ApiKeyManager.vue')),
  webhook: defineAsyncComponent(() => import('@/views/admin/WebhookSettings.vue')),
  'ai-settings': defineAsyncComponent(() => import('@/views/admin/AiSettings.vue')),
  'ai-scan': defineAsyncComponent(() => import('@/views/admin/AiScanTaskManager.vue')),
  query: defineAsyncComponent(() => import('@/views/admin/QueryConsole.vue')),
  'data-export': defineAsyncComponent(() => import('@/views/admin/DataMigrationPage.vue')),
  etl: defineAsyncComponent(() => import('@/views/admin/EtlTaskManager.vue')),
  'export-scripts': defineAsyncComponent(() => import('@/views/admin/ExportScriptManager.vue')),
  'validation-scripts': defineAsyncComponent(() => import('@/views/admin/ValidationScriptManager.vue')),
  'operation-log': defineAsyncComponent(() => import('@/views/admin/OperationLog.vue')),
  backup: defineAsyncComponent(() => import('@/views/admin/BackupManager.vue')),
  'system-settings': defineAsyncComponent(() => import('@/views/admin/SystemSettings.vue')),
}
