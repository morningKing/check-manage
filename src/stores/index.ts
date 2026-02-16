/**
 * Store 统一导出
 *
 * 集中导出所有 Pinia Store，方便其他模块引用
 */

// 应用全局状态
export { useAppStore } from './app'

// 菜单状态
export { useMenuStore } from './menu'

// 页面配置状态
export { usePageConfigStore } from './pageConfig'

// 认证状态
export { useAuthStore } from './auth'

// 标签页状态
export { useTabStore } from './tab'
