/**
 * 菜单管理页面
 *
 * 职责：
 * - 可视化展示和编辑菜单树
 * - 支持菜单的增删改操作
 * - 支持菜单排序和层级调整
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
                新增顶级菜单
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
            <template #default="{ node, data }">
              <div class="tree-node">
                <el-icon v-if="data.icon" class="node-icon">
                  <component :is="data.icon" />
                </el-icon>
                <span class="node-label">{{ data.name }}</span>
                <div class="node-actions">
                  <el-button
                    v-if="getNodeLevel(data) < 3"
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
            label-width="100px"
          >
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

            <el-form-item label="父级菜单" v-if="!isAddingRoot">
              <el-input :value="parentMenuName" disabled />
            </el-form-item>

            <el-form-item>
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
import { Plus } from '@element-plus/icons-vue'
import { useMenuStore, usePageConfigStore } from '@/stores'
import { ConfirmDialog } from '@/components/common'
import type { MenuItem, MenuFormData } from '@/types'
import { createEmptyMenuFormData, ROLE_OPTIONS } from '@/types'

// ==================== Store ====================

const menuStore = useMenuStore()
const pageConfigStore = usePageConfigStore()

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
 * 表单标题
 */
const formTitle = computed(() => {
  if (isEditMode.value) return '编辑菜单'
  if (isAddingRoot.value) return '新增顶级菜单'
  return '新增子菜单'
})

/**
 * 父级菜单名称
 */
const parentMenuName = computed(() => {
  if (!formData.value.parentId) return '-'
  const parent = menuStore.getMenuById(formData.value.parentId)
  return parent?.name || '-'
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
    pageId: data.pageId || null,
    parentId: data.parentId || null,
    order: data.order,
    path: data.path || '',
    roles: data.roles || ['admin', 'developer', 'guest']
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
  formData.value = {
    ...createEmptyMenuFormData(),
    parentId: parent.id,
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
  } catch (error) {
    ElMessage.error('删除失败')
  }
}

/**
 * 处理表单提交
 */
async function handleSubmit(): Promise<void> {
  const valid = await formRef.value?.validate()
  if (!valid) return

  submitLoading.value = true
  try {
    if (isEditMode.value && formData.value.id) {
      // 更新
      await menuStore.updateMenu(formData.value.id, {
        name: formData.value.name,
        icon: formData.value.icon,
        pageId: formData.value.pageId,
        parentId: formData.value.parentId,
        order: formData.value.order,
        path: formData.value.path,
        roles: formData.value.roles
      })
      ElMessage.success('更新成功')
    } else {
      // 新增
      await menuStore.addMenu({
        name: formData.value.name,
        icon: formData.value.icon,
        pageId: formData.value.pageId,
        parentId: formData.value.parentId,
        order: formData.value.order,
        path: formData.value.path,
        roles: formData.value.roles
      })
      ElMessage.success('创建成功')
      resetForm()
    }
  } catch (error) {
    ElMessage.error(isEditMode.value ? '更新失败' : '创建失败')
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
