/**
 * 角色与权限管理 store（管理员）
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getRoles, getRole, getPermissionCatalog, createRole, updateRole, deleteRole,
} from '@/api/role'
import type { Role, RoleDetail, PermissionCatalogItem } from '@/types'

export const useRoleStore = defineStore('role', () => {
  const roles = ref<Role[]>([])
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

  return { roles, catalog, loading, loadRoles, loadCatalog, fetchRole, saveRole, addRole, removeRole }
})
