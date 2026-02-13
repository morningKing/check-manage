/**
 * 页面配置管理页面
 *
 * 职责：
 * - 管理页面配置列表
 * - 支持页面配置的增删改
 * - 集成字段配置编辑器
 *
 * 布局：
 * - 左侧：页面配置列表
 * - 右侧：页面详情和字段配置编辑器
 */
<template>
  <div class="page-config-manager">
    <el-row :gutter="20" class="full-height">
      <!-- 左侧：页面配置列表 -->
      <el-col :span="8">
        <el-card class="list-card">
          <template #header>
            <div class="card-header">
              <span>页面配置列表</span>
              <el-button type="primary" size="small" @click="handleAdd">
                <el-icon><Plus /></el-icon>
                新增
              </el-button>
            </div>
          </template>

          <div class="page-list">
            <div
              v-for="config in pageConfigs"
              :key="config.id"
              class="page-item"
              :class="{ active: currentPageId === config.id }"
              @click="handleSelect(config)"
            >
              <div class="page-info">
                <div class="page-name">{{ config.name }}</div>
                <div class="page-meta">
                  {{ config.fields.length }} 个字段
                </div>
              </div>
              <div class="page-actions">
                <el-button
                  type="danger"
                  link
                  size="small"
                  @click.stop="handleDeleteConfirm(config)"
                >
                  删除
                </el-button>
              </div>
            </div>

            <el-empty v-if="pageConfigs.length === 0" description="暂无页面配置" />
          </div>
        </el-card>
      </el-col>

      <!-- 右侧：页面详情和字段配置 -->
      <el-col :span="16">
        <el-card class="detail-card">
          <template #header>
            <div class="card-header">
              <span>{{ detailTitle }}</span>
            </div>
          </template>

          <div v-if="showDetail" class="page-detail">
            <!-- 页面基本信息 -->
            <el-form
              ref="formRef"
              :model="formData"
              :rules="formRules"
              label-width="100px"
              class="page-form"
            >
              <el-form-item label="页面名称" prop="name">
                <el-input
                  v-model="formData.name"
                  placeholder="请输入页面名称"
                  maxlength="50"
                />
              </el-form-item>

              <el-form-item label="页面描述" prop="description">
                <el-input
                  v-model="formData.description"
                  type="textarea"
                  placeholder="请输入页面描述"
                  :rows="2"
                />
              </el-form-item>

              <el-form-item label="API端点" prop="apiEndpoint">
                <el-input
                  v-model="formData.apiEndpoint"
                  placeholder="如：/api/data/inspection-case"
                />
              </el-form-item>

              <el-form-item>
                <el-button
                  type="primary"
                  @click="handleSavePageInfo"
                  :loading="saveLoading"
                >
                  保存基本信息
                </el-button>
              </el-form-item>
            </el-form>

            <el-divider content-position="left">字段配置</el-divider>

            <!-- 字段配置编辑器 -->
            <FieldConfigEditor
              :page-id="currentPageId!"
              :fields="currentFields"
              @update="handleFieldsUpdate"
            />
          </div>

          <el-empty v-else description="请选择或新增页面配置" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 新增页面对话框 -->
    <el-dialog
      v-model="addDialogVisible"
      title="新增页面配置"
      width="500px"
      :close-on-click-modal="false"
    >
      <el-form
        ref="addFormRef"
        :model="addFormData"
        :rules="formRules"
        label-width="100px"
      >
        <el-form-item label="页面名称" prop="name">
          <el-input
            v-model="addFormData.name"
            placeholder="请输入页面名称"
            maxlength="50"
          />
        </el-form-item>

        <el-form-item label="页面描述" prop="description">
          <el-input
            v-model="addFormData.description"
            type="textarea"
            placeholder="请输入页面描述"
            :rows="2"
          />
        </el-form-item>

        <el-form-item label="API端点" prop="apiEndpoint">
          <el-input
            v-model="addFormData.apiEndpoint"
            placeholder="如：/api/data/my-page"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="addDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleCreate" :loading="createLoading">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 删除确认对话框 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      :message="`确定要删除页面配置「${pageToDelete?.name}」吗？删除后相关菜单将失去关联。`"
      type="danger"
      confirm-text="删除"
      @confirm="handleDelete"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * PageConfigManager 组件脚本
 *
 * 功能：
 * 1. 页面配置列表展示
 * 2. 页面配置的增删改
 * 3. 字段配置编辑
 */
import { ref, computed, onMounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { usePageConfigStore } from '@/stores'
import { ConfirmDialog } from '@/components/common'
import FieldConfigEditor from './FieldConfigEditor.vue'
import type { PageConfig, PageFormData, FieldConfig } from '@/types'
import { createEmptyPageFormData } from '@/types'

// ==================== Store ====================

const pageConfigStore = usePageConfigStore()

// ==================== Refs ====================

const formRef = ref<FormInstance>()
const addFormRef = ref<FormInstance>()

// ==================== State ====================

/**
 * 当前选中的页面ID
 */
const currentPageId = ref<string | null>(null)

/**
 * 页面表单数据
 */
const formData = ref<PageFormData>(createEmptyPageFormData())

/**
 * 新增表单数据
 */
const addFormData = ref<PageFormData>(createEmptyPageFormData())

/**
 * 新增对话框可见性
 */
const addDialogVisible = ref(false)

/**
 * 删除对话框可见性
 */
const deleteDialogVisible = ref(false)

/**
 * 待删除的页面配置
 */
const pageToDelete = ref<PageConfig | null>(null)

/**
 * 保存加载状态
 */
const saveLoading = ref(false)

/**
 * 创建加载状态
 */
const createLoading = ref(false)

// ==================== 常量 ====================

/**
 * 表单验证规则
 */
const formRules: FormRules = {
  name: [
    { required: true, message: '请输入页面名称', trigger: 'blur' },
    { min: 2, max: 50, message: '长度在 2 到 50 个字符', trigger: 'blur' }
  ],
  apiEndpoint: [
    { required: true, message: '请输入API端点', trigger: 'blur' }
  ]
}

// ==================== 计算属性 ====================

/**
 * 页面配置列表
 */
const pageConfigs = computed(() => pageConfigStore.pageConfigs)

/**
 * 当前页面配置
 */
const currentPageConfig = computed(() => {
  if (!currentPageId.value) return null
  return pageConfigStore.getPageConfigById(currentPageId.value)
})

/**
 * 当前页面字段列表
 */
const currentFields = computed(() => {
  return currentPageConfig.value?.fields || []
})

/**
 * 是否显示详情
 */
const showDetail = computed(() => currentPageId.value !== null)

/**
 * 详情标题
 */
const detailTitle = computed(() => {
  return currentPageConfig.value?.name || '页面详情'
})

// ==================== 方法 ====================

/**
 * 处理选择页面配置
 */
function handleSelect(config: PageConfig): void {
  currentPageId.value = config.id
  formData.value = {
    id: config.id,
    name: config.name,
    description: config.description || '',
    apiEndpoint: config.apiEndpoint
  }
}

/**
 * 处理新增
 */
function handleAdd(): void {
  addFormData.value = createEmptyPageFormData()
  addDialogVisible.value = true
}

/**
 * 处理创建
 */
async function handleCreate(): Promise<void> {
  const valid = await addFormRef.value?.validate()
  if (!valid) return

  createLoading.value = true
  try {
    const created = await pageConfigStore.addPageConfig({
      name: addFormData.value.name,
      description: addFormData.value.description,
      apiEndpoint: addFormData.value.apiEndpoint,
      fields: []
    })
    ElMessage.success('创建成功')
    addDialogVisible.value = false
    // 选中新创建的页面
    handleSelect(created)
  } catch (error) {
    ElMessage.error('创建失败')
  } finally {
    createLoading.value = false
  }
}

/**
 * 处理保存页面基本信息
 */
async function handleSavePageInfo(): Promise<void> {
  const valid = await formRef.value?.validate()
  if (!valid || !currentPageId.value) return

  saveLoading.value = true
  try {
    await pageConfigStore.updatePageConfig(currentPageId.value, {
      name: formData.value.name,
      description: formData.value.description,
      apiEndpoint: formData.value.apiEndpoint,
      fields: currentFields.value
    })
    ElMessage.success('保存成功')
  } catch (error) {
    ElMessage.error('保存失败')
  } finally {
    saveLoading.value = false
  }
}

/**
 * 处理字段配置更新
 */
async function handleFieldsUpdate(fields: FieldConfig[]): Promise<void> {
  if (!currentPageId.value) return

  try {
    await pageConfigStore.updatePageFields(currentPageId.value, fields)
    ElMessage.success('字段配置已更新')
  } catch (error) {
    ElMessage.error('更新失败')
  }
}

/**
 * 处理删除确认
 */
function handleDeleteConfirm(config: PageConfig): void {
  pageToDelete.value = config
  deleteDialogVisible.value = true
}

/**
 * 处理删除
 */
async function handleDelete(): Promise<void> {
  if (!pageToDelete.value) return

  try {
    await pageConfigStore.deletePageConfig(pageToDelete.value.id)
    ElMessage.success('删除成功')
    deleteDialogVisible.value = false
    // 如果删除的是当前选中的，清空选择
    if (currentPageId.value === pageToDelete.value.id) {
      currentPageId.value = null
    }
  } catch (error) {
    ElMessage.error('删除失败')
  }
}

// ==================== 生命周期 ====================

onMounted(async () => {
  if (pageConfigStore.pageConfigs.length === 0) {
    await pageConfigStore.fetchPageConfigs()
  }
})
</script>

<style scoped lang="scss">
.page-config-manager {
  height: 100%;
}

.full-height {
  height: 100%;
}

.list-card,
.detail-card {
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

.page-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.page-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    border-color: #409eff;
    background-color: #f5f7fa;
  }

  &.active {
    border-color: #409eff;
    background-color: #ecf5ff;
  }

  .page-info {
    .page-name {
      font-weight: 500;
      color: #303133;
    }

    .page-meta {
      font-size: 12px;
      color: #909399;
      margin-top: 4px;
    }
  }

  .page-actions {
    opacity: 0;
    transition: opacity 0.2s;
  }

  &:hover .page-actions {
    opacity: 1;
  }
}

.page-detail {
  .page-form {
    max-width: 600px;
  }
}
</style>
