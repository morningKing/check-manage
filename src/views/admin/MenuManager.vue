/**
 * 菜单管理页面
 *
 * 职责：
 * - 可视化展示和编辑菜单树
 * - 支持菜单的增删改操作
 * - 支持菜单类型选择（workspace/project/data）
 * - 工作空间-项目-数据三层层级结构
 *
 * 功能：
 * - 左侧：菜单树展示
 * - 右侧：菜单编辑表单
 */
<template>
  <div class="menu-manager">
    <el-row :gutter="20" class="full-height">
      <!-- 左侧：菜单树 -->
      <el-col :span="10">
        <el-card class="tree-card">
          <template #header>
            <div class="card-header">
              <span>菜单结构</span>
              <el-button type="primary" size="small" @click="handleAddRoot">
                <el-icon><Plus /></el-icon>
                新增工作空间
              </el-button>
            </div>
          </template>

          <el-tree
            ref="treeRef"
            :data="menuTree"
            :props="treeProps"
            node-key="id"
            default-expand-all
            highlight-current
            :expand-on-click-node="false"
            @node-click="handleNodeClick"
          >
            <template #default="{ data }">
              <div class="tree-node">
                <el-icon v-if="data.icon" class="node-icon">
                  <component :is="data.icon" />
                </el-icon>
                <span class="node-label">{{ data.name }}</span>
                <el-tag :type="getMenuTypeTag(data.menuType)" size="small" style="margin-left: 8px">
                  {{ getMenuTypeLabel(data.menuType) }}
                </el-tag>
                <el-icon v-if="data.menuType === 'system'" class="system-lock">
                  <Lock />
                </el-icon>
                <div class="node-actions" v-if="data.menuType !== 'system'">
                  <el-button
                    v-if="canAddChild(data)"
                    type="primary"
                    link
                    size="small"
                    @click.stop="handleAddChild(data)"
                  >
                    添加子菜单
                  </el-button>
                  <el-button
                    type="danger"
                    link
                    size="small"
                    @click.stop="handleDeleteConfirm(data)"
                  >
                    删除
                  </el-button>
                </div>
              </div>
            </template>
          </el-tree>
        </el-card>
      </el-col>

      <!-- 右侧：编辑表单 -->
      <el-col :span="14">
        <el-card class="form-card">
          <template #header>
            <div class="card-header">
              <span>{{ formTitle }}</span>
            </div>
          </template>

          <el-form
            v-if="showForm"
            ref="formRef"
            :model="formData"
            :rules="formRules"
            :disabled="isCurrentSystemMenu"
            label-width="100px"
          >
            <el-alert
              v-if="isCurrentSystemMenu"
              type="info"
              :closable="false"
              title="系统菜单不可修改"
              description="系统菜单不允许编辑或删除。"
              show-icon
              style="margin-bottom: 16px"
            />

            <el-form-item label="菜单类型" prop="menuType">
              <el-select
                v-model="formData.menuType"
                placeholder="请选择菜单类型"
                style="width: 100%"
                :disabled="isEditMode"
              >
                <el-option
                  v-for="opt in availableMenuTypeOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                  :disabled="opt.disabled"
                >
                  <span>{{ opt.label }}</span>
                  <span style="color: #909399; font-size: 12px; margin-left: 8px">{{ opt.description }}</span>
                </el-option>
              </el-select>
              <div class="form-tip">
                工作空间(一级) → 项目(二级) → 数据菜单(三级)
              </div>
            </el-form-item>

            <el-form-item label="菜单名称" prop="name">
              <el-input
                v-model="formData.name"
                placeholder="请输入菜单名称"
                maxlength="20"
                show-word-limit
              />
            </el-form-item>

            <el-form-item label="菜单图标" prop="icon">
              <el-select
                v-model="formData.icon"
                placeholder="请选择图标"
                filterable
                style="width: 100%"
              >
                <el-option
                  v-for="icon in iconOptions"
                  :key="icon"
                  :label="icon"
                  :value="icon"
                >
                  <el-icon style="margin-right: 8px">
                    <component :is="icon" />
                  </el-icon>
                  <span>{{ icon }}</span>
                </el-option>
              </el-select>
            </el-form-item>

            <el-form-item label="路由路径" prop="path">
              <el-input
                v-model="formData.path"
                placeholder="请输入路由路径，如 /inspection/case"
              />
            </el-form-item>

            <el-form-item label="关联页面" prop="pageId" v-if="formData.menuType === 'data'">
              <el-select
                v-model="formData.pageId"
                placeholder="请选择关联的页面配置"
                clearable
                style="width: 100%"
              >
                <el-option
                  v-for="page in pageOptions"
                  :key="page.value"
                  :label="page.label"
                  :value="page.value"
                />
              </el-select>
              <div class="form-tip">
                数据菜单必须关联页面配置
              </div>
            </el-form-item>

            <el-form-item label="排序序号" prop="order">
              <el-input-number
                v-model="formData.order"
                :min="1"
                :max="999"
                controls-position="right"
                style="width: 100%"
              />
            </el-form-item>

            <el-form-item label="可见角色" prop="roles">
              <el-checkbox-group v-model="formData.roles">
                <el-checkbox
                  v-for="opt in ROLE_OPTIONS"
                  :key="opt.value"
                  :label="opt.value"
                  :value="opt.value"
                >
                  {{ opt.label }}
                </el-checkbox>
              </el-checkbox-group>
            </el-form-item>

            <el-form-item label="父级菜单" prop="parentId" v-if="formData.menuType !== 'workspace'">
              <el-select
                v-model="formData.parentId"
                placeholder="请选择父级菜单"
                style="width: 100%"
                :disabled="isAddingRoot"
              >
                <el-option
                  v-for="opt in availableParentOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                  :disabled="opt.disabled"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="导出脚本" prop="exportScriptId">
              <el-select
                v-model="formData.exportScriptId"
                placeholder="请选择导出脚本（可选）"
                clearable
                style="width: 100%"
              >
                <el-option
                  v-for="opt in exportScriptOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>

            <el-form-item v-if="!isCurrentSystemMenu">
              <el-button type="primary" @click="handleSubmit" :loading="submitLoading">
                {{ isEditMode ? '更新' : '创建' }}
              </el-button>
              <el-button @click="resetForm">重置</el-button>
            </el-form-item>
          </el-form>

          <el-empty v-else description="请选择或新增菜单项" />
        </el-card>
      </el-col>
    </el-row>
                v-model="formData.name"
                placeholder="请输入菜单名称"
                maxlength="20"
                show-word-limit
              />
            </el-form-item>

            <el-form-item label="菜单图标" prop="icon">
              <el-select
                v-model="formData.icon"
                placeholder="请选择图标"
                filterable
                style="width: 100%"
              >
                <el-option
                  v-for="icon in iconOptions"
                  :key="icon"
                  :label="icon"
                  :value="icon"
                >
                  <el-icon style="margin-right: 8px">
                    <component :is="icon" />
                  </el-icon>
                  <span>{{ icon }}</span>
                </el-option>
              </el-select>
            </el-form-item>

            <el-form-item label="路由路径" prop="path">
              <el-input
                v-model="formData.path"
                placeholder="请输入路由路径，如 /inspection/case"
              />
            </el-form-item>

            <el-form-item label="关联页面" prop="pageId">
              <el-select
                v-model="formData.pageId"
                placeholder="请选择关联的页面配置"
                clearable
                style="width: 100%"
              >
                <el-option
                  v-for="page in pageOptions"
                  :key="page.value"
                  :label="page.label"
                  :value="page.value"
                />
              </el-select>
              <div class="form-tip">
                仅叶子菜单需要关联页面
              </div>
            </el-form-item>

            <el-form-item label="排序序号" prop="order">
              <el-input-number
                v-model="formData.order"
                :min="1"
                :max="999"
                controls-position="right"
                style="width: 100%"
              />
            </el-form-item>

            <el-form-item label="可见角色" prop="roles">
              <el-checkbox-group v-model="formData.roles">
                <el-checkbox
                  v-for="opt in ROLE_OPTIONS"
                  :key="opt.value"
                  :label="opt.value"
                  :value="opt.value"
                >
                  {{ opt.label }}
                </el-checkbox>
              </el-checkbox-group>
              <div class="form-tip">
                选择哪些角色可以看到此菜单
              </div>
            </el-form-item>

            <el-form-item label="父级菜单" prop="parentId">
              <el-select
                v-model="formData.parentId"
                placeholder="请选择父级菜单（可选）"
                clearable
                style="width: 100%"
                :disabled="isAddingRoot"
              >
                <el-option
                  v-for="opt in availableParentOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                  :disabled="opt.disabled"
                />
              </el-select>
              <div class="form-tip">
                选择父级菜单将创建子菜单，留空则为顶级菜单
              </div>
            </el-form-item>

            <el-form-item label="导出脚本" prop="exportScriptId">
              <el-select
                v-model="formData.exportScriptId"
                placeholder="请选择导出脚本（可选）"
                clearable
                style="width: 100%"
              >
                <el-option
                  v-for="opt in exportScriptOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
              <div class="form-tip">
                绑定导出脚本后，在数据导出页面可一键导出此菜单下所有数据
              </div>
            </el-form-item>

            <el-form-item v-if="!isCurrentSystemMenu">
              <el-button type="primary" @click="handleSubmit" :loading="submitLoading">
                {{ isEditMode ? '更新' : '创建' }}
              </el-button>
              <el-button @click="resetForm">重置</el-button>
            </el-form-item>
          </el-form>

          <el-empty v-else description="请选择或新增菜单项" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 删除确认对话框 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      :message="deleteMessage"
      type="danger"
      confirm-text="删除"
      @confirm="handleDelete"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * MenuManager 组件脚本
 *
 * 功能：
 * 1. 展示菜单树结构
 * 2. 菜单的增删改操作
 * 3. 工作空间-项目-数据三层层级结构
 */
import { ref, computed, onMounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Plus, Lock } from '@element-plus/icons-vue'
import { useMenuStore, usePageConfigStore, useExportScriptStore } from '@/stores'
import { ConfirmDialog } from '@/components/common'
import type { MenuItem, MenuFormData, MenuType } from '@/types'
import { createEmptyMenuFormData, ROLE_OPTIONS, MENU_TYPE_OPTIONS } from '@/types'

// ==================== Store ====================

const menuStore = useMenuStore()
const pageConfigStore = usePageConfigStore()
const exportScriptStore = useExportScriptStore()

// ==================== Refs ====================

const treeRef = ref()
const formRef = ref<FormInstance>()

// ==================== State ====================

const formData = ref<MenuFormData>(createEmptyMenuFormData())
const showForm = ref(false)
const isEditMode = ref(false)
const isAddingRoot = ref(false)
const submitLoading = ref(false)
const deleteDialogVisible = ref(false)
const menuToDelete = ref<MenuItem | null>(null)

// ==================== 常量 ====================

const treeProps = {
  children: 'children',
  label: 'name'
}

const iconOptions = [
  'House', 'Monitor', 'Document', 'Files', 'Calendar', 'Tickets',
  'Clock', 'Flag', 'Setting', 'Menu', 'User', 'Search',
  'Edit', 'Delete', 'Plus', 'Minus', 'Check', 'Close',
  'Warning', 'InfoFilled', 'Star', 'Location', 'Phone', 'Message'
]

const formRules: FormRules = {
  name: [
    { required: true, message: '请输入菜单名称', trigger: 'blur' },
    { min: 2, max: 20, message: '长度在 2 到 20 个字符', trigger: 'blur' }
  ],
  icon: [
    { required: true, message: '请选择菜单图标', trigger: 'change' }
  ],
  menuType: [
    { required: true, message: '请选择菜单类型', trigger: 'change' }
  ],
  order: [
    { required: true, message: '请输入排序序号', trigger: 'blur' }
  ],
  pageId: [
    {
      required: true,
      message: '数据菜单必须关联页面配置',
      trigger: 'change',
      validator: (_rule, value, callback) => {
        if (formData.value.menuType === 'data' && !value) {
          callback(new Error('数据菜单必须关联页面配置'))
        } else {
          callback()
        }
      }
    }
  ]
}

// ==================== 计算属性 ====================

const menuTree = computed(() => menuStore.menuTree)
const pageOptions = computed(() => pageConfigStore.pageConfigOptions)
const exportScriptOptions = computed(() => exportScriptStore.scriptOptions)

const formTitle = computed(() => {
  if (isEditMode.value) return '编辑菜单'
  if (isAddingRoot.value) return '新增工作空间'
  return '新增子菜单'
})

const deleteMessage = computed(() => {
  if (!menuToDelete.value) return ''
  const childCount = menuStore.getChildMenus(menuToDelete.value.id).length
  if (childCount > 0) {
    return `确定要删除菜单"${menuToDelete.value.name}"及其 ${childCount} 个子菜单吗？`
  }
  return `确定要删除菜单"${menuToDelete.value.name}"吗？`
})

// ==================== 菜单类型相关函数 ====================

function getMenuTypeLabel(type: MenuType): string {
  const labels: Record<MenuType, string> = {
    system: '系统',
    workspace: '工作空间',
    project: '项目',
    data: '数据'
  }
  return labels[type] || type
}

function getMenuTypeTag(type: MenuType): '' | 'success' | 'warning' | 'info' | 'danger' {
  const tags: Record<MenuType, '' | 'success' | 'warning' | 'info' | 'danger'> = {
    system: 'info',
    workspace: '',
    project: 'success',
    data: 'warning'
  }
  return tags[type] || ''
}

function canAddChild(menu: MenuItem): boolean {
  // workspace 可以添加 project
  // project 可以添加 data
  // system 和 data 不能添加子菜单
  return menu.menuType === 'workspace' || menu.menuType === 'project'
}

/**
 * 可选的菜单类型选项（根据父级动态计算）
 */
const availableMenuTypeOptions = computed(() => {
  const parent = formData.value.parentId
    ? menuStore.getMenuById(formData.value.parentId)
    : null

  if (!parent) {
    // 无父级（顶级）：只能选 workspace
    return MENU_TYPE_OPTIONS.filter(opt => opt.value === 'workspace')
  }

  if (parent.menuType === 'workspace') {
    // 父级是 workspace：只能选 project
    return MENU_TYPE_OPTIONS.filter(opt => opt.value === 'project')
  }

  if (parent.menuType === 'project') {
    // 父级是 project：只能选 data
    return MENU_TYPE_OPTIONS.filter(opt => opt.value === 'data')
  }

  return []
})

/**
 * 当前编辑的菜单是否为系统菜单
 */
const isCurrentSystemMenu = computed(() => {
  if (!isEditMode.value || !formData.value.id) return false
  const menu = menuStore.getMenuById(formData.value.id)
  return menu ? menu.menuType === 'system' : false
})

/**
 * 可选的父级菜单选项
 */
const availableParentOptions = computed(() => {
  const options: Array<{ value: string; label: string; disabled?: boolean }> = []

  function buildOptions(menus: MenuItem[], prefix: string = '') {
    menus.forEach(menu => {
      // 排除系统菜单
      if (menu.menuType === 'system') return

      // 排除当前编辑的菜单及其子菜单
      if (isEditMode.value && formData.value.id) {
        if (menu.id === formData.value.id) return
        if (isDescendant(formData.value.id, menu.id)) return
      }

      // 根据当前菜单类型限制可选父级
      const menuType = formData.value.menuType
      if (menuType === 'project' && menu.menuType !== 'workspace') return
      if (menuType === 'data' && menu.menuType !== 'project') return

      options.push({
        value: menu.id,
        label: prefix + menu.name
      })

      if (menu.children && menu.children.length > 0) {
        buildOptions(menu.children, prefix + menu.name + ' / ')
      }
    })
  }

  buildOptions(menuTree.value)
  return options
})

function isDescendant(sourceId: string, targetId: string): boolean {
  const target = menuStore.getMenuById(targetId)
  if (!target) return false

  let current: MenuItem | undefined = target
  while (current) {
    if (current.parentId === sourceId) return true
    current = current.parentId ? menuStore.getMenuById(current.parentId) : undefined
  }
  return false
}

// ==================== 方法 ====================

/**
 * 处理节点点击
 */
function handleNodeClick(data: MenuItem): void {
  isEditMode.value = true
  isAddingRoot.value = false
  formData.value = {
    id: data.id,
    name: data.name,
    icon: data.icon || 'Document',
    menuType: data.menuType,
    pageId: data.pageId || null,
    parentId: data.parentId || null,
    projectId: data.projectId || null,
    order: data.order,
    path: data.path || '',
    roles: data.roles || ['admin', 'developer', 'guest'],
    exportScriptId: data.exportScriptId || null
  }
  showForm.value = true
}

/**
 * 处理添加顶级菜单（工作空间）
 */
function handleAddRoot(): void {
  isEditMode.value = false
  isAddingRoot.value = true
  formData.value = {
    ...createEmptyMenuFormData(),
    menuType: 'workspace',
    parentId: null,
    order: menuStore.topLevelMenus.length + 1
  }
  showForm.value = true
  formRef.value?.clearValidate()
}

/**
 * 处理添加子菜单
 */
function handleAddChild(parent: MenuItem): void {
  isEditMode.value = false
  isAddingRoot.value = false
  const children = menuStore.getChildMenus(parent.id)

  // 根据父级类型确定子菜单类型
  let childType: MenuType = 'data'
  if (parent.menuType === 'workspace') {
    childType = 'project'
  }

  formData.value = {
    ...createEmptyMenuFormData(),
    menuType: childType,
    parentId: parent.id,
    projectId: parent.menuType === 'project' ? parent.id : null,
    order: children.length + 1
  }
  showForm.value = true
  formRef.value?.clearValidate()
}

/**
 * 处理删除确认
 */
function handleDeleteConfirm(menu: MenuItem): void {
  menuToDelete.value = menu
  deleteDialogVisible.value = true
}

/**
 * 处理删除
 */
async function handleDelete(): Promise<void> {
  if (!menuToDelete.value) return

  try {
    await menuStore.deleteMenu(menuToDelete.value.id)
    ElMessage.success('删除成功')
    deleteDialogVisible.value = false
    showForm.value = false
    formData.value = createEmptyMenuFormData()
  } catch (error: any) {
    const msg = error.response?.data?.error
    ElMessage.error(msg || '删除失败')
  }
}

/**
 * 处理表单提交
 */
async function handleSubmit(): Promise<void> {
  const valid = await formRef.value?.validate()
  if (!valid) return

  // 验证数据菜单必须关联页面
  if (formData.value.menuType === 'data' && !formData.value.pageId) {
    ElMessage.error('数据菜单必须关联页面配置')
    return
  }

  // 验证父级菜单选择
  if (formData.value.menuType !== 'workspace' && formData.value.parentId) {
    if (isEditMode.value && formData.value.id) {
      // 检查是否选择了自己作为父级
      if (formData.value.parentId === formData.value.id) {
        ElMessage.error('不能将自己设为父级菜单')
        return
      }

      // 检查是否选择了自己的子菜单作为父级（循环引用）
      if (isDescendant(formData.value.id, formData.value.parentId)) {
        ElMessage.error('不能将子菜单设为父级菜单')
        return
      }
    }

    // 验证父级类型匹配
    const parent = menuStore.getMenuById(formData.value.parentId)
    if (parent) {
      if (formData.value.menuType === 'project' && parent.menuType !== 'workspace') {
        ElMessage.error('项目菜单的父级必须是工作空间')
        return
      }
      if (formData.value.menuType === 'data' && parent.menuType !== 'project') {
        ElMessage.error('数据菜单的父级必须是项目')
        return
      }
    }
  }

  submitLoading.value = true
  try {
    if (isEditMode.value && formData.value.id) {
      // 更新
      await menuStore.updateMenu(formData.value.id, {
        name: formData.value.name,
        icon: formData.value.icon,
        menuType: formData.value.menuType,
        pageId: formData.value.pageId,
        parentId: formData.value.parentId,
        projectId: formData.value.projectId,
        order: formData.value.order,
        path: formData.value.path,
        roles: formData.value.roles,
        exportScriptId: formData.value.exportScriptId
      })
      ElMessage.success('更新成功')
      const updated = menuStore.getMenuById(formData.value.id)
      if (updated) {
        formData.value = {
          id: updated.id,
          name: updated.name,
          icon: updated.icon || 'Document',
          menuType: updated.menuType,
          pageId: updated.pageId || null,
          parentId: updated.parentId || null,
          projectId: updated.projectId || null,
          order: updated.order,
          path: updated.path || '',
          roles: updated.roles || ['admin', 'developer', 'guest'],
          exportScriptId: updated.exportScriptId || null
        }
      }
    } else {
      // 新增
      await menuStore.addMenu({
        name: formData.value.name,
        icon: formData.value.icon,
        menuType: formData.value.menuType,
        pageId: formData.value.pageId,
        parentId: formData.value.parentId,
        projectId: formData.value.projectId,
        order: formData.value.order,
        path: formData.value.path,
        roles: formData.value.roles,
        exportScriptId: formData.value.exportScriptId
      })
      ElMessage.success('创建成功')
      resetForm()
    }
  } catch (error: any) {
    const msg = error.response?.data?.error
    ElMessage.error(msg || (isEditMode.value ? '更新失败' : '创建失败'))
  } finally {
    submitLoading.value = false
  }
}

/**
 * 重置表单
 */
function resetForm(): void {
  if (isAddingRoot.value) {
    formData.value = {
      ...createEmptyMenuFormData(),
      parentId: null,
      order: menuStore.topLevelMenus.length + 1
    }
  } else if (formData.value.parentId) {
    const children = menuStore.getChildMenus(formData.value.parentId)
    formData.value = {
      ...createEmptyMenuFormData(),
      parentId: formData.value.parentId,
      order: children.length + 1
    }
  }
  formRef.value?.clearValidate()
}

// ==================== 生命周期 ====================

onMounted(async () => {
  // 确保数据已加载
  if (menuStore.menuList.length === 0) {
    await menuStore.fetchMenus()
  }
  if (pageConfigStore.pageConfigs.length === 0) {
    await pageConfigStore.fetchPageConfigs()
  }
  if (exportScriptStore.scripts.length === 0) {
    await exportScriptStore.fetchScripts()
  }
})
</script>

<style scoped lang="scss">
.menu-manager {
  height: 100%;
}

.full-height {
  height: 100%;
}

.tree-card,
.form-card {
  height: 100%;

  :deep(.el-card__body) {
    height: calc(100% - 60px);
    overflow: auto;
  }
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.tree-node {
  display: flex;
  align-items: center;
  width: 100%;
  padding-right: 8px;

  .node-icon {
    margin-right: 8px;
    color: #409eff;
  }

  .node-label {
    flex: 1;
  }

  .system-lock {
    margin-left: 6px;
    color: #909399;
    font-size: 14px;
  }

  .node-actions {
    display: none;
  }

  &:hover .node-actions {
    display: flex;
    gap: 4px;
  }
}

.form-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
</style>
