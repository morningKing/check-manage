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

          <el-input
            v-model="searchKeyword"
            placeholder="搜索页面名称..."
            clearable
            :prefix-icon="Search"
            class="list-search"
          />

          <div class="page-list">
            <div
              v-for="config in filteredPageConfigs"
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

            <el-empty v-if="filteredPageConfigs.length === 0" :description="searchKeyword ? '无匹配结果' : '暂无页面配置'" />
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

              <el-form-item label="导出脚本">
                <el-select
                  v-model="formData.exportScripts"
                  multiple
                  clearable
                  placeholder="选择整页导出脚本"
                  style="width: 100%"
                >
                  <el-option
                    v-for="s in pageExportScripts"
                    :key="s.id"
                    :label="`${s.name} (${s.outputFormat})`"
                    :value="s.id"
                  />
                </el-select>
              </el-form-item>

              <el-form-item label="行级导出">
                <el-select
                  v-model="formData.rowExportScripts"
                  multiple
                  clearable
                  placeholder="选择单行导出脚本"
                  style="width: 100%"
                >
                  <el-option
                    v-for="s in rowExportScripts"
                    :key="s.id"
                    :label="`${s.name} (${s.outputFormat})`"
                    :value="s.id"
                  />
                </el-select>
              </el-form-item>

              <el-form-item label="Open API">
                <el-switch
                  v-model="formData.apiPublic"
                  active-text="公开"
                  inactive-text="关闭"
                />
              </el-form-item>

              <el-form-item label="允许写入" v-if="formData.apiPublic">
                <el-switch
                  v-model="formData.apiWritable"
                  active-text="允许"
                  inactive-text="只读"
                />
                <div style="color: #909399; font-size: 12px; margin-top: 4px">
                  开启后外部系统可通过 Open API 新增和修改数据
                </div>
              </el-form-item>

              <el-form-item label="校验脚本">
                <el-select
                  v-model="formData.validationScript"
                  clearable
                  placeholder="选择校验脚本（可选）"
                  style="width: 100%"
                >
                  <el-option
                    v-for="s in allValidationScripts"
                    :key="s.id"
                    :label="s.name"
                    :value="s.id"
                  />
                </el-select>
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
import { ref, computed, onMounted, onActivated } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import { usePageConfigStore } from '@/stores'
import { ConfirmDialog } from '@/components/common'
import FieldConfigEditor from './FieldConfigEditor.vue'
import type { PageConfig, PageFormData, FieldConfig } from '@/types'
import type { ExportScript } from '@/types'
import type { ValidationScript } from '@/types'
import { createEmptyPageFormData } from '@/types'
import { getExportScripts } from '@/api/exportScript'
import { getValidationScripts } from '@/api/validationScript'

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
 * 搜索关键词
 */
const searchKeyword = ref('')

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

/**
 * 所有导出脚本
 */
const allExportScripts = ref<ExportScript[]>([])

/**
 * 所有校验脚本
 */
const allValidationScripts = ref<ValidationScript[]>([])

// ==================== 计算属性（脚本筛选） ====================

const pageExportScripts = computed(() =>
  allExportScripts.value.filter(s => (s.scope || 'page') === 'page')
)

const rowExportScripts = computed(() =>
  allExportScripts.value.filter(s => s.scope === 'row')
)

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
 * 搜索过滤后的页面配置列表
 */
const filteredPageConfigs = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) return pageConfigs.value
  return pageConfigs.value.filter(c => c.name.toLowerCase().includes(kw))
})

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
    apiEndpoint: config.apiEndpoint,
    exportScripts: config.exportScripts || [],
    rowExportScripts: config.rowExportScripts || [],
    apiPublic: config.apiPublic || false,
    apiWritable: config.apiWritable || false,
    validationScript: config.validationScript || '',
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
      exportScripts: formData.value.exportScripts || [],
      rowExportScripts: formData.value.rowExportScripts || [],
      apiPublic: formData.value.apiPublic,
      apiWritable: formData.value.apiPublic ? formData.value.apiWritable : false,
      validationScript: formData.value.validationScript || null,
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

onActivated(async () => {
  try {
    allExportScripts.value = await getExportScripts()
  } catch {
    // ignore
  }
  try {
    allValidationScripts.value = await getValidationScripts()
  } catch {
    // ignore
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

.list-search {
  margin-bottom: 12px;
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
