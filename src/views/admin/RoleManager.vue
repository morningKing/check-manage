<template>
  <div class="role-manager">
    <el-card class="role-list-card" v-loading="roleStore.loading">
      <template #header>
        <div class="card-header">
          <span>角色</span>
          <el-button type="primary" size="small" @click="openCreate">新建角色</el-button>
        </div>
      </template>
      <el-empty v-if="roleStore.roles.length === 0" description="暂无角色" :image-size="80" />
      <el-menu v-else :default-active="selectedId" @select="selectRole">
        <el-menu-item v-for="r in roleStore.roles" :key="r.id" :index="r.id">
          <span>{{ r.name }}</span>
          <el-tag v-if="r.isSuperuser" type="danger" size="small" style="margin-left:8px">超管</el-tag>
          <el-tag v-else-if="r.isSystem" type="info" size="small" style="margin-left:8px">内置</el-tag>
        </el-menu-item>
      </el-menu>
    </el-card>

    <el-card v-if="detail" class="role-editor-card">
      <template #header>
        <div class="card-header">
          <span>{{ detail.name }} — 权限配置</span>
          <div>
            <el-button v-if="!detail.isSystem" type="danger" size="small" @click="onDelete">删除角色</el-button>
            <el-button type="primary" size="small" :disabled="detail.isSuperuser" @click="onSave">保存</el-button>
          </div>
        </div>
      </template>

      <el-alert v-if="detail.isSuperuser" type="info" :closable="false"
        title="超级管理员拥有全部权限，不可修改。" style="margin-bottom:16px" />

      <el-tabs v-model="activeTab">
        <el-tab-pane label="管理功能" name="admin">
          <div v-for="group in groupedCatalog" :key="group.name" class="perm-group">
            <h4>{{ group.name }}</h4>
            <el-checkbox
              v-for="item in group.items" :key="item.key"
              :model-value="detail.isSuperuser ? true : adminKeys.has(item.key)"
              :disabled="detail.isSuperuser"
              @change="(v: boolean) => toggleAdminKey(item.key, v)"
            >{{ item.label }}</el-checkbox>
          </div>
        </el-tab-pane>

        <el-tab-pane label="数据页权限" name="pages">
          <el-form-item label="未配置数据页默认">
            <el-radio-group v-model="defaultPageAccess" :disabled="detail.isSuperuser">
              <el-radio value="none">无</el-radio>
              <el-radio value="read">只读</el-radio>
              <el-radio value="write">读写</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-table :data="pageRows" border height="420">
            <el-table-column prop="name" label="数据页" />
            <el-table-column label="读" width="70" align="center">
              <template #default="{ row }">
                <el-checkbox v-model="row.canRead" :disabled="detail!.isSuperuser" /></template>
            </el-table-column>
            <el-table-column label="增" width="70" align="center">
              <template #default="{ row }">
                <el-checkbox v-model="row.canCreate" :disabled="detail!.isSuperuser" /></template>
            </el-table-column>
            <el-table-column label="改" width="70" align="center">
              <template #default="{ row }">
                <el-checkbox v-model="row.canUpdate" :disabled="detail!.isSuperuser" /></template>
            </el-table-column>
            <el-table-column label="删" width="70" align="center">
              <template #default="{ row }">
                <el-checkbox v-model="row.canDelete" :disabled="detail!.isSuperuser" /></template>
            </el-table-column>
          </el-table>
          <p class="hint">未在表中勾选的数据页按上面的“默认”计算。仅保存有任意勾选的行。</p>
        </el-tab-pane>

        <el-tab-pane label="菜单可见性" name="menus">
          <el-alert v-if="detail.isSuperuser" type="info" :closable="false"
            title="超级管理员可见全部菜单，无需配置。" style="margin-bottom:12px" />
          <template v-else>
            <p class="hint" style="margin-top:0">勾选此角色可在侧边栏看到的菜单。注意：子菜单要显示，其父级也需勾选。</p>
            <el-tree
              ref="menuTreeRef"
              :key="'menutree-' + detail.id"
              :data="menuTreeData"
              show-checkbox
              check-strictly
              node-key="id"
              :props="{ label: 'name', children: 'children' }"
              :default-checked-keys="checkedMenuKeys"
              default-expand-all
              style="max-height:460px;overflow:auto"
            />
          </template>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="createVisible" title="新建角色" width="420px">
      <el-form :model="createForm" label-width="90px">
        <el-form-item label="名称"><el-input v-model="createForm.name" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="createForm.description" /></el-form-item>
        <el-form-item label="默认数据页">
          <el-radio-group v-model="createForm.defaultPageAccess">
            <el-radio value="none">无</el-radio>
            <el-radio value="read">只读</el-radio>
            <el-radio value="write">读写</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoleStore } from '@/stores/role'
import { usePageConfigStore } from '@/stores/pageConfig'
import { getMenuList } from '@/api/menu'
import type { RoleDetail, DefaultPageAccess, PermissionCatalogItem, MenuItem } from '@/types'

const roleStore = useRoleStore()
const pageConfigStore = usePageConfigStore()

const selectedId = ref('')
const detail = ref<RoleDetail | null>(null)
const activeTab = ref('admin')
const adminKeys = ref<Set<string>>(new Set())
const defaultPageAccess = ref<DefaultPageAccess>('read')
const pageRows = ref<Array<{ pageId: string; name: string; canRead: boolean; canCreate: boolean; canUpdate: boolean; canDelete: boolean }>>([])

// 菜单可见性
const menuTreeRef = ref()
const allMenus = ref<MenuItem[]>([])
const menuTreeData = ref<MenuItem[]>([])
const checkedMenuKeys = ref<string[]>([])

function buildMenuTree(flat: MenuItem[]): MenuItem[] {
  const map = new Map<string, MenuItem & { children: MenuItem[] }>()
  flat.forEach(m => map.set(m.id, { ...m, children: [] }))
  const roots: MenuItem[] = []
  flat.forEach(m => {
    const node = map.get(m.id)!
    const parent = m.parentId ? map.get(m.parentId) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  })
  return roots
}

const createVisible = ref(false)
const createForm = ref<{ name: string; description: string; defaultPageAccess: DefaultPageAccess }>({
  name: '', description: '', defaultPageAccess: 'read',
})

const groupedCatalog = computed(() => {
  const groups: Record<string, PermissionCatalogItem[]> = {}
  for (const item of roleStore.catalog) {
    (groups[item.group] ||= []).push(item)
  }
  return Object.entries(groups).map(([name, items]) => ({ name, items }))
})

async function selectRole(id: string): Promise<void> {
  selectedId.value = id
  const d = await roleStore.fetchRole(id)
  detail.value = d
  adminKeys.value = new Set(d.adminKeys)
  defaultPageAccess.value = d.defaultPageAccess
  const configured = new Map(d.pagePermissions.map(p => [p.pageId, p]))
  // build rows from all page configs
  const pages = pageConfigStore.pageConfigs
  pageRows.value = pages.map((pc) => {
    const c = configured.get(pc.id)
    return {
      pageId: pc.id, name: pc.name,
      canRead: c?.canRead ?? false, canCreate: c?.canCreate ?? false,
      canUpdate: c?.canUpdate ?? false, canDelete: c?.canDelete ?? false,
    }
  })
  // 菜单可见性：拉取全部菜单（含 roles），计算该角色已勾选的菜单
  allMenus.value = await getMenuList()
  menuTreeData.value = buildMenuTree(allMenus.value)
  checkedMenuKeys.value = allMenus.value
    .filter(m => (m.roles || []).includes(id))
    .map(m => m.id)
}

function toggleAdminKey(key: string, on: boolean): void {
  if (on) adminKeys.value.add(key)
  else adminKeys.value.delete(key)
}

async function onSave(): Promise<void> {
  if (!detail.value) return
  const pagePermissions = pageRows.value
    .filter(r => r.canRead || r.canCreate || r.canUpdate || r.canDelete)
    .map(r => ({ pageId: r.pageId, canRead: r.canRead, canCreate: r.canCreate, canUpdate: r.canUpdate, canDelete: r.canDelete }))
  await roleStore.saveRole(detail.value.id, {
    adminKeys: [...adminKeys.value],
    defaultPageAccess: defaultPageAccess.value,
    pagePermissions,
  })
  // 同时保存菜单可见性（超管无需配置）
  if (!detail.value.isSuperuser) {
    const menuIds = (menuTreeRef.value?.getCheckedKeys(false) ?? checkedMenuKeys.value) as string[]
    await roleStore.saveMenuVisibility(detail.value.id, menuIds)
  }
  ElMessage.success('已保存')
}

function openCreate(): void {
  createForm.value = { name: '', description: '', defaultPageAccess: 'read' }
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!createForm.value.name.trim()) { ElMessage.warning('请输入名称'); return }
  const res = await roleStore.addRole(createForm.value)
  createVisible.value = false
  ElMessage.success('已创建')
  await selectRole(res.id)
}

async function onDelete(): Promise<void> {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm(`确定删除角色「${detail.value.name}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  await roleStore.removeRole(detail.value.id)
  detail.value = null
  selectedId.value = ''
  ElMessage.success('已删除')
}

onMounted(async () => {
  await Promise.all([roleStore.loadRoles(), roleStore.loadCatalog(), pageConfigStore.fetchPageConfigs()])
  if (roleStore.roles.length) await selectRole(roleStore.roles[0].id)
})
</script>

<style scoped lang="scss">
.role-manager { display: flex; gap: 16px; height: 100%; }
.role-list-card { width: 240px; flex-shrink: 0; }
.role-editor-card { flex: 1; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.perm-group { margin-bottom: 16px; h4 { margin: 8px 0; } }
.hint { color: #909399; font-size: 12px; margin-top: 8px; }
</style>
