/**
 * 角色与权限管理 store（管理员）
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getRoles, getRole, getRoleOptions, getPermissionCatalog,
  createRole, updateRole, deleteRole, updateRoleMenuVisibility,
} from '@/api/role'
import type { Role, RoleDetail, RoleOption, PermissionCatalogItem } from '@/types'

export const useRoleStore = defineStore('role', () => {
  const roles = ref<Role[]>([])
  /** 轻量角色选项（供菜单/用户管理等下拉选择器使用） */
  const options = ref<RoleOption[]>([])
  const catalog = ref<PermissionCatalogItem[]>([])
  const loading = ref(false)

  async function loadRoles(): Promise<void> {
    loading.value = true
    try {
      roles.value = await getRoles()
    } finally {
      loading.value = false
    }
  }

  /** 加载轻量角色选项（仅需登录，不要求 admin.roles） */
  async function loadOptions(): Promise<void> {
    options.value = await getRoleOptions()
  }

  /** 保存某角色的菜单可见性 */
  async function saveMenuVisibility(id: string, menuIds: string[]): Promise<void> {
    await updateRoleMenuVisibility(id, menuIds)
  }

  async function loadCatalog(): Promise<void> {
    if (catalog.value.length) return
    catalog.value = await getPermissionCatalog()
  }

  function fetchRole(id: string): Promise<RoleDetail> {
    return getRole(id)
  }

  async function saveRole(id: string, data: Partial<RoleDetail>): Promise<void> {
    await updateRole(id, data)
    await loadRoles()
  }

  async function addRole(data: Partial<Role>): Promise<{ id: string; name: string }> {
    const res = await createRole(data)
    await loadRoles()
    return res
  }

  async function removeRole(id: string): Promise<void> {
    await deleteRole(id)
    await loadRoles()
  }

  return {
    roles, options, catalog, loading,
    loadRoles, loadOptions, loadCatalog, fetchRole, saveRole, addRole, removeRole,
    saveMenuVisibility,
  }
})
