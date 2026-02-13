/**
 * 本地存储工具函数
 *
 * 封装 localStorage 操作，提供类型安全的存取方法
 * 主要用于持久化配置数据的备份
 */

/**
 * 存储键名常量
 *
 * 集中管理所有存储键，避免硬编码
 */
export const STORAGE_KEYS = {
  /** 菜单配置 */
  MENU_CONFIG: 'check-manage:menus',
  /** 页面配置 */
  PAGE_CONFIGS: 'check-manage:pageConfigs',
  /** 应用设置 */
  APP_SETTINGS: 'check-manage:settings',
  /** 侧边栏折叠状态 */
  SIDEBAR_COLLAPSED: 'check-manage:sidebarCollapsed',
  /** 认证令牌 */
  AUTH_TOKEN: 'check-manage:token',
  /** 用户信息 */
  USER_INFO: 'check-manage:userInfo',
} as const

/**
 * 设置存储数据
 *
 * 将数据序列化为 JSON 字符串后存储
 *
 * @param key - 存储键名
 * @param value - 要存储的数据
 */
export function setStorage<T>(key: string, value: T): void {
  try {
    const serialized = JSON.stringify(value)
    localStorage.setItem(key, serialized)
  } catch (error) {
    console.error(`存储数据失败 [${key}]:`, error)
  }
}

/**
 * 获取存储数据
 *
 * 从存储中读取数据并反序列化
 *
 * @param key - 存储键名
 * @param defaultValue - 默认值（键不存在时返回）
 * @returns 存储的数据或默认值
 */
export function getStorage<T>(key: string, defaultValue: T): T {
  try {
    const serialized = localStorage.getItem(key)
    if (serialized === null) {
      return defaultValue
    }
    return JSON.parse(serialized) as T
  } catch (error) {
    console.error(`读取存储数据失败 [${key}]:`, error)
    return defaultValue
  }
}

/**
 * 移除存储数据
 *
 * @param key - 存储键名
 */
export function removeStorage(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch (error) {
    console.error(`移除存储数据失败 [${key}]:`, error)
  }
}

/**
 * 清除所有应用相关存储
 *
 * 只清除以 'check-manage:' 开头的存储项
 */
export function clearAppStorage(): void {
  try {
    const keysToRemove: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key && key.startsWith('check-manage:')) {
        keysToRemove.push(key)
      }
    }
    keysToRemove.forEach((key) => localStorage.removeItem(key))
  } catch (error) {
    console.error('清除存储数据失败:', error)
  }
}

/**
 * 检查存储是否可用
 *
 * @returns 存储是否可用
 */
export function isStorageAvailable(): boolean {
  try {
    const testKey = '__storage_test__'
    localStorage.setItem(testKey, testKey)
    localStorage.removeItem(testKey)
    return true
  } catch {
    return false
  }
}
