/**
 * 菜单相关 API 接口
 *
 * 封装菜单配置的 CRUD 操作
 */

import { get, post, put, del } from '@/utils/request'
import { getStorage, STORAGE_KEYS } from '@/utils/storage'
import type { MenuItem, MenuExportPreview } from '@/types'

/**
 * 获取菜单列表
 */
export function getMenuList() {
  return get<MenuItem[]>('/menus')
}

/**
 * 获取菜单详情
 *
 * @param id - 菜单ID
 */
export function getMenuById(id: string) {
  return get<MenuItem>(`/menus/${id}`)
}

/**
 * 创建菜单
 *
 * @param menu - 菜单数据
 */
export function createMenu(menu: Omit<MenuItem, 'children'>) {
  return post<MenuItem>('/menus', menu)
}

/**
 * 更新菜单
 *
 * @param id - 菜单ID
 * @param menu - 更新的菜单数据
 */
export function updateMenu(id: string, menu: Partial<MenuItem>) {
  return put<MenuItem>(`/menus/${id}`, { ...menu, id })
}

/**
 * 删除菜单
 *
 * @param id - 菜单ID
 */
export function deleteMenu(id: string) {
  return del(`/menus/${id}`)
}

/**
 * 设置菜单的导出脚本绑定
 *
 * @param menuId - 菜单ID
 * @param exportScriptId - 导出脚本ID（null 表示取消绑定）
 */
export function setMenuExportScript(menuId: string, exportScriptId: string | null) {
  return put<{ id: string; exportScriptId: string | null; exportScriptName?: string }>(
    `/menus/${menuId}/exportScript`,
    { exportScriptId }
  )
}

/**
 * 获取菜单导出预览
 *
 * @param menuId - 菜单ID
 */
export function getMenuExportPreview(menuId: string) {
  return get<{
    menuName: string
    pages: Array<{ collection: string; pageName: string; recordCount: number }>
    boundScript?: { id: string; name: string } | null
    totalRecords: number
  }>(`/menus/${menuId}/exportPreview`)
}

// ==================== 菜单级导出 API ====================

/**
 * 获取可用于导出的菜单树（仅包含动态数据表）
 *
 * 过滤掉系统配置、数据工具等静态页面菜单
 */
export function getAvailableExportMenus() {
  return get<MenuItem[]>('/menuExport/availableMenus')
}

/**
 * 预览菜单导出信息
 *
 * @param menuIds - 菜单ID列表
 */
export function previewMenuExport(menuIds: string[], branchId = 'main') {
  return post<MenuExportPreview>('/menuExport/preview', { menuIds, branchId })
}

/**
 * 执行菜单级导出（绑定驱动：每个数据页用其绑定的导出脚本，无绑定则跳过）
 *
 * @param menuIds - 菜单ID列表
 * @param branchId - 分支ID（默认 main）
 * @returns { blob, notice } - ZIP 文件 + 可选的「跳过/告警」提示（来自 X-Export-Errors 头）
 */
export async function executeMenuExport(
  menuIds: string[],
  branchId = 'main'
): Promise<{ blob: Blob; notice: string }> {
  // 令牌存于 STORAGE_KEYS.AUTH_TOKEN（check-manage:token），与 axios 拦截器一致。
  const token = getStorage<string>(STORAGE_KEYS.AUTH_TOKEN, '')
  const response = await fetch('/api/menuExport', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({ menuIds, branchId })
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: '导出失败' }))
    throw new Error(error.error || '导出失败')
  }

  // 后端把「跳过/部分失败」摘要放在 X-Export-Errors 头（URL 编码）
  let notice = ''
  const raw = response.headers.get('X-Export-Errors')
  if (raw) {
    try { notice = decodeURIComponent(raw) } catch { notice = raw }
  }
  return { blob: await response.blob(), notice }
}

/**
 * 批量清空多个数据页在指定分支的全部记录
 *
 * @param collections - collection 名称列表
 * @param branchId - 分支ID（默认 main）
 */
export function batchClearCollections(collections: string[], branchId = 'main') {
  return post<{
    perCollection: Record<string, number>
    totalDeleted: number
    relationsDeleted: number
  }>('/menuExport/batchClear', { collections, branchId })
}
