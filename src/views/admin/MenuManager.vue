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
              <el-select
                v-model="formData.roles"
                multiple
                filterable
                collapse-tags
                collapse-tags-tooltip
                placeholder="选择可见此菜单的角色（可搜索）"
                style="width: 100%"
              >
                <el-option
                  v-for="opt in roleStore.options"
                  :key="opt.id"
                  :label="opt.name"
                  :value="opt.id"
                />
              </el-select>
              <div class="form-tip">
                选择哪些角色可以看到此菜单（含自定义角色）。超级管理员始终可见全部。
              </div>
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
 * 3. 支持最多3级菜单
 */
import { ref, computed, onMounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Plus, Lock } from '@element-plus/icons-vue'
import { useMenuStore, usePageConfigStore, useExportScriptStore, useRoleStore } from '@/stores'
import { ConfirmDialog } from '@/components/common'
import type { MenuItem, MenuFormData, MenuType } from '@/types'
import { createEmptyMenuFormData, MENU_TYPE_OPTIONS } from '@/types'

// ==================== Store ====================

const menuStore = useMenuStore()
const pageConfigStore = usePageConfigStore()
const exportScriptStore = useExportScriptStore()
const roleStore = useRoleStore()

// ==================== Refs ====================

const treeRef = ref()
const formRef = ref<FormInstance>()

// ==================== State ====================

/**
 * 表单数据
 */
const formData = ref<MenuFormData>(createEmptyMenuFormData())

/**
 * 是否显示表单
 */
const showForm = ref(false)

/**
 * 是否编辑模式
 */
const isEditMode = ref(false)

/**
 * 是否添加顶级菜单
 */
const isAddingRoot = ref(false)

/**
 * 提交加载状态
 */
const submitLoading = ref(false)

/**
 * 删除对话框可见性
 */
const deleteDialogVisible = ref(false)

/**
 * 待删除的菜单项
 */
const menuToDelete = ref<MenuItem | null>(null)

// ==================== 常量 ====================

/**
 * 树形控件属性配置
 */
const treeProps = {
  children: 'children',
  label: 'name'
}

/**
 * 可用图标列表
 */
const iconOptions = [
  'House', 'Monitor', 'Document', 'Files', 'Calendar', 'Tickets',
  'Clock', 'Flag', 'Setting', 'Menu', 'User', 'Search',
  'Edit', 'Delete', 'Plus', 'Minus', 'Check', 'Close',
  'Warning', 'InfoFilled', 'Star', 'Location', 'Phone', 'Message'
]

/**
 * 表单验证规则
 */
const formRules: FormRules = {
  name: [
    { required: true, message: '请输入菜单名称', trigger: 'blur' },
    { min: 2, max: 20, message: '长度在 2 到 20 个字符', trigger: 'blur' }
  ],
  icon: [
    { required: true, message: '请选择菜单图标', trigger: 'change' }
  ],
  order: [
    { required: true, message: '请输入排序序号', trigger: 'blur' }
  ]
}

// ==================== 计算属性 ====================

/**
 * 菜单树数据
 */
const menuTree = computed(() => menuStore.menuTree)

/**
 * 页面配置选项
 */
const pageOptions = computed(() => pageConfigStore.pageConfigOptions)

/**
 * 导出脚本选项
 */
const exportScriptOptions = computed(() => exportScriptStore.scriptOptions)

/**
 * 表单标题
 */
const formTitle = computed(() => {
  if (isEditMode.value) return '编辑菜单'
  if (isAddingRoot.value) return '新增工作空间'
  if (formData.value.menuType === 'project') return '新增项目'
  return '新增数据菜单'
})

/**
 * 删除确认消息
 */
const deleteMessage = computed(() => {
  if (!menuToDelete.value) return ''
  const childCount = menuStore.getChildMenus(menuToDelete.value.id).length
  if (childCount > 0) {
    return `确定要删除菜单"${menuToDelete.value.name}"及其 ${childCount} 个子菜单吗？`
  }
  return `确定要删除菜单"${menuToDelete.value.name}"吗？`
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
 * 获取菜单类型标签
 */
function getMenuTypeLabel(menuType?: MenuType): string {
  const labels: Record<string, string> = {
    system: '系统',
    workspace: '工作空间',
    project: '项目',
    data: '数据',
  }
  return labels[menuType || 'data'] || '数据'
}

/**
 * 获取菜单类型标签颜色
 */
function getMenuTypeTag(menuType?: MenuType): string {
  const tags: Record<string, string> = {
    system: 'info',
    workspace: 'primary',
    project: 'warning',
    data: 'success',
  }
  return tags[menuType || 'data'] || 'success'
}

/**
 * 判断是否可以添加子菜单
 */
function canAddChild(menu: MenuItem): boolean {
  // 系统菜单不允许添加子菜单
  if (menu.menuType === 'system') return false
  // 工作空间只能添加项目
  if (menu.menuType === 'workspace') return true
  // 项目只能添加数据菜单
  if (menu.menuType === 'project') return true
  // 数据菜单不允许添加子菜单
  return false
}

/**
 * 可用的菜单类型选项
 * 根据是否添加顶级菜单动态调整
 */
const availableMenuTypeOptions = computed(() => {
  if (isAddingRoot.value) {
    // 顶级菜单只能是工作空间
    return MENU_TYPE_OPTIONS.filter(opt => opt.value === 'workspace')
  }

  // 根据父级菜单类型确定可用的子菜单类型
  if (formData.value.parentId) {
    const parent = menuStore.getMenuById(formData.value.parentId)
    if (parent) {
      if (parent.menuType === 'workspace') {
        return MENU_TYPE_OPTIONS.filter(opt => opt.value === 'project')
      }
      if (parent.menuType === 'project') {
        return MENU_TYPE_OPTIONS.filter(opt => opt.value === 'data')
      }
    }
  }

  // 默认显示所有（会被父级限制）
  return MENU_TYPE_OPTIONS
})

/**
 * 可选的父级菜单选项
 * 根据菜单类型规则过滤
 *
 * 特殊规则：数据菜单当前挂在工作空间下时（非标准层级），
 * 允许将其父级改为工作空间或项目，用于将错位菜单归位。
 * 其他情况严格按层级规则：项目→工作空间，数据→项目。
 */
const availableParentOptions = computed(() => {
  const options: Array<{ value: string; label: string; disabled?: boolean }> = []

  // 判断当前数据菜单是否挂在工作空间下（非标准层级）
  function isDataMisplacedUnderWorkspace(): boolean {
    if (!isEditMode.value || formData.value.menuType !== 'data' || !formData.value.parentId) return false
    const parent = menuStore.getMenuById(formData.value.parentId)
    return parent?.menuType === 'workspace'
  }

  // 递归构建菜单选项
  function buildOptions(menus: MenuItem[], prefix: string = '') {
    menus.forEach(menu => {
      // 排除系统菜单
      if (menu.menuType === 'system') return

      // 排除当前编辑的菜单及其子菜单
      if (isEditMode.value && formData.value.id) {
        if (menu.id === formData.value.id) return
        if (isDescendant(formData.value.id, menu.id)) return
      }

      const targetMenuType = formData.value.menuType
      if (targetMenuType === 'project') {
        // 项目只能选择工作空间作为父级
        if (menu.menuType !== 'workspace') return
      } else if (targetMenuType === 'data') {
        // 标准情况：数据菜单只能选择项目作为父级
        if (!isDataMisplacedUnderWorkspace() && menu.menuType !== 'project') return
        // 非标准情况（挂在 workspace 下）：允许选择 workspace 或 project
        if (isDataMisplacedUnderWorkspace() && menu.menuType !== 'workspace' && menu.menuType !== 'project') return
      }

      const label = prefix + menu.name
      options.push({ value: menu.id, label })

      // 递归处理子菜单（只继续处理可以作为父级的类型）
      if (menu.children && menu.children.length > 0) {
        // 工作空间可以继续展开找项目，项目可以继续展开找数据菜单的父级候选
        if (menu.menuType === 'workspace' || menu.menuType === 'project') {
          buildOptions(menu.children, label + ' / ')
        }
      }
    })
  }

  buildOptions(menuTree.value)

  return options
})

/**
 * 检查目标菜单是否是源菜单的后代
 */
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
 * 获取节点层级
 */
function getNodeLevel(node: MenuItem): number {
  let level = 1
  let current = node
  while (current.parentId) {
    level++
    const parent = menuStore.getMenuById(current.parentId)
    if (!parent) break
    current = parent
  }
  return level
}

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
    menuType: data.menuType || 'data',
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
 * 处理添加顶级菜单
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

  // 根据父级菜单类型确定子菜单类型
  let childMenuType: MenuType = 'data'
  if (parent.menuType === 'workspace') {
    childMenuType = 'project'
  } else if (parent.menuType === 'project') {
    childMenuType = 'data'
  }

  formData.value = {
    ...createEmptyMenuFormData(),
    menuType: childMenuType,
    parentId: parent.id,
    projectId: childMenuType === 'data' ? parent.id : null,
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

  // 验证父级菜单选择
  if (isEditMode.value && formData.value.id && formData.value.parentId) {
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

    // 检查层级限制（最多3级）
    const newParent = menuStore.getMenuById(formData.value.parentId)
    if (newParent) {
      const newParentLevel = getNodeLevel(newParent)
      if (newParentLevel >= 3) {
        ElMessage.error('父级菜单不能是第3级菜单（会导致第4级）')
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
      // 更新成功后刷新表单数据
      const updated = menuStore.getMenuById(formData.value.id)
      if (updated) {
        formData.value = {
          id: updated.id,
          name: updated.name,
          icon: updated.icon || 'Document',
          menuType: updated.menuType || 'data',
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
  if (roleStore.options.length === 0) {
    await roleStore.loadOptions()
  }
})
</script>

<style scoped lang="scss">
.menu-manager {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.full-height {
  height: 100%;
  flex: 1;

  // 确保 el-col 继承高度
  > .el-col {
    height: 100%;
  }
}

// 使用 flex 布局让 card body 可滚动
.tree-card,
.form-card {
  height: 100%;
  display: flex;
  flex-direction: column;

  :deep(.el-card__body) {
    flex: 1;
    min-height: 0;
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
