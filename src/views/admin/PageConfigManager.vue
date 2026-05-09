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
      <!-- 左侧：页面配置列表（已抽取为独立组件） -->
      <el-col :span="8">
        <el-card class="list-card">
          <template #header>
            <span>页面配置列表</span>
          </template>
          <PageConfigList
            v-model="currentPageId"
            :configs="pageConfigs"
            @add="handleAdd"
          />
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
            <el-tabs v-model="activeTab" type="border-card">
              <!-- Tab 1: 基本信息 -->
              <el-tab-pane label="基本信息" name="basic">
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
                      保存
                    </el-button>
                  </el-form-item>
                </el-form>
              </el-tab-pane>

              <!-- Tab 2: 视图配置 -->
              <el-tab-pane label="视图配置" name="view">
                <el-form label-width="100px" class="page-form">
                  <el-form-item label="默认视图">
                    <el-select v-model="kanbanDefaultView" placeholder="表格" style="width: 200px">
                      <el-option label="表格视图" value="table" />
                      <el-option label="看板视图" value="kanban" />
                    </el-select>
                  </el-form-item>

                  <el-form-item label="分组字段">
                    <el-select v-model="kanbanGroupField" clearable placeholder="选择 select 类型字段" style="width: 100%">
                      <el-option
                        v-for="f in selectTypeFields"
                        :key="f.fieldName"
                        :label="f.label"
                        :value="f.fieldName"
                      />
                    </el-select>
                    <div style="color: #909399; font-size: 12px; margin-top: 4px">
                      选择一个下拉选择类型的字段作为看板列的分组依据
                    </div>
                  </el-form-item>

                  <template v-if="kanbanGroupField">
                    <el-form-item label="卡片标题">
                      <el-select v-model="kanbanCardTitle" placeholder="选择标题字段" style="width: 100%">
                        <el-option
                          v-for="f in currentFields"
                          :key="f.fieldName"
                          :label="f.label"
                          :value="f.fieldName"
                        />
                      </el-select>
                    </el-form-item>

                    <el-form-item label="卡片摘要">
                      <el-select v-model="kanbanCardFields" multiple placeholder="选择显示字段" style="width: 100%">
                        <el-option
                          v-for="f in currentFields"
                          :key="f.fieldName"
                          :label="f.label"
                          :value="f.fieldName"
                        />
                      </el-select>
                    </el-form-item>

                    <el-form-item label="颜色字段">
                      <el-select v-model="kanbanColorField" clearable placeholder="可选：按此字段着色" style="width: 100%">
                        <el-option
                          v-for="f in selectTypeFields"
                          :key="f.fieldName"
                          :label="f.label"
                          :value="f.fieldName"
                        />
                      </el-select>
                    </el-form-item>
                  </template>

                  <el-empty v-if="selectTypeFields.length === 0" :image-size="60" description="暂无 select 类型字段，请先在「字段配置」中添加" />

                  <el-form-item>
                    <el-button
                      type="primary"
                      @click="handleSavePageInfo"
                      :loading="saveLoading"
                    >
                      保存
                    </el-button>
                  </el-form-item>
                </el-form>
              </el-tab-pane>

              <!-- Tab 3: 字段配置 -->
              <el-tab-pane label="字段配置" name="fields">
                <FieldConfigEditor
                  :page-id="currentPageId!"
                  :fields="currentFields"
                  @update="handleFieldsUpdate"
                />
              </el-tab-pane>

              <!-- Tab 4: 删除绑定 -->
              <el-tab-pane label="删除绑定" name="deleteBinding">
                <el-form label-width="100px" class="page-form">
                  <el-form-item label="启用删除绑定">
                    <el-switch
                      v-model="deleteBindingEnabled"
                      active-text="启用"
                      inactive-text="关闭"
                    />
                    <div style="color: #909399; font-size: 12px; margin-top: 4px">
                      启用后，删除数据时会弹出表单让用户填写信息，保存后再执行删除
                    </div>
                  </el-form-item>

                  <template v-if="deleteBindingEnabled">
                    <el-form-item label="目标集合" required>
                      <el-select
                        v-model="deleteBindingTargetCollection"
                        placeholder="选择目标集合"
                        style="width: 100%"
                      >
                        <el-option
                          v-for="c in availableTargetCollections"
                          :key="c.id"
                          :label="c.name"
                          :value="c.id"
                        />
                      </el-select>
                      <div style="color: #909399; font-size: 12px; margin-top: 4px">
                        删除记录时，表单数据将保存到此集合
                      </div>
                    </el-form-item>

                    <el-form-item label="对话框标题">
                      <el-input
                        v-model="deleteBindingDialogTitle"
                        placeholder="如：设备报废登记"
                        clearable
                      />
                    </el-form-item>

                    <el-form-item label="对话框宽度">
                      <el-input
                        v-model="deleteBindingDialogWidth"
                        placeholder="500px"
                        style="width: 200px"
                      />
                    </el-form-item>

                    <el-form-item label="自动填充">
                      <el-switch
                        v-model="deleteBindingAutoFillOperator"
                        active-text="操作者信息"
                        inactive-text="关闭"
                      />
                      <div style="color: #909399; font-size: 12px; margin-top: 4px">
                        自动填充操作者用户名、删除时间、源记录ID等信息
                      </div>
                    </el-form-item>

                    <el-divider content-position="left">继承字段映射</el-divider>

                    <div class="inherit-fields-config">
                      <div
                        v-for="(mapping, index) in deleteBindingInheritFields"
                        :key="index"
                        class="inherit-field-item"
                      >
                        <el-select
                          v-model="mapping.sourceField"
                          placeholder="源字段"
                          style="width: 180px"
                        >
                          <el-option
                            v-for="f in currentFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                        <el-icon class="arrow-icon"><Right /></el-icon>
                        <el-select
                          v-model="mapping.targetField"
                          placeholder="目标字段"
                          style="width: 180px"
                          :disabled="!deleteBindingTargetCollection"
                        >
                          <el-option
                            v-for="f in targetCollectionFields"
                            :key="f.fieldName"
                            :label="f.label"
                            :value="f.fieldName"
                          />
                        </el-select>
                        <el-button
                          type="danger"
                          link
                          @click="removeInheritField(index)"
                        >
                          删除
                        </el-button>
                      </div>
                      <el-button type="primary" link @click="addInheritField">
                        + 添加继承字段
                      </el-button>
                    </div>

                    <el-alert type="info" :closable="false" show-icon style="margin-top: 16px">
                      <template #title>
                        表单字段将自动使用目标集合的字段配置，继承字段会自动填充无需用户填写。
                      </template>
                    </el-alert>
                  </template>

                  <el-empty v-else :image-size="60" description="启用删除绑定以配置" />

                  <el-form-item style="margin-top: 20px">
                    <el-button
                      type="primary"
                      @click="handleSaveDeleteBinding"
                      :loading="saveLoading"
                    >
                      保存
                    </el-button>
                  </el-form-item>
                </el-form>
              </el-tab-pane>

              <!-- Tab 5: 关系图谱 -->
              <el-tab-pane label="关系图谱" name="relations">
                <PageConfigRelationGraph
                  :page-id="currentPageId!"
                  @navigate="handleNavigateToPage"
                />
              </el-tab-pane>
            </el-tabs>
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
import { ref, computed, watch, onMounted, onActivated } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Right } from '@element-plus/icons-vue'
import { usePageConfigStore } from '@/stores'
import { useMenuStore } from '@/stores'
import FieldConfigEditor from './FieldConfigEditor.vue'
import PageConfigList from './components/PageConfigList.vue'
import PageConfigRelationGraph from '@/components/PageConfigRelationGraph.vue'
import type { PageFormData, FieldConfig, DeleteBindingConfig, InheritFieldMapping } from '@/types'
import type { ExportScript } from '@/types'
import type { ValidationScript } from '@/types'
import { createEmptyPageFormData } from '@/types'
import { getExportScripts } from '@/api/exportScript'
import { getValidationScripts } from '@/api/validationScript'

// ==================== Store ====================

const pageConfigStore = usePageConfigStore()
const menuStore = useMenuStore()

// ==================== Refs ====================

const formRef = ref<FormInstance>()
const addFormRef = ref<FormInstance>()

// ==================== State ====================

/**
 * 当前选中的页面ID
 */
const currentPageId = ref<string | null>(null)

/**
 * 当前激活的 Tab
 */
const activeTab = ref('basic')

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

/**
 * 看板配置响应式状态
 */
const kanbanDefaultView = ref<'table' | 'kanban' | 'excel' | 'calendar'>('table')
const kanbanGroupField = ref('')
const kanbanCardTitle = ref('')
const kanbanCardFields = ref<string[]>([])
const kanbanColorField = ref('')

/**
 * 删除绑定配置响应式状态
 */
const deleteBindingEnabled = ref(false)
const deleteBindingTargetCollection = ref('')
const deleteBindingDialogTitle = ref('')
const deleteBindingDialogWidth = ref('500px')
const deleteBindingAutoFillOperator = ref(true)
const deleteBindingInheritFields = ref<InheritFieldMapping[]>([])

// ==================== 计算属性（脚本筛选） ====================

const pageExportScripts = computed(() =>
  allExportScripts.value.filter(s => (s.scope || 'page') === 'page')
)

const rowExportScripts = computed(() =>
  allExportScripts.value.filter(s => s.scope === 'row')
)

const selectTypeFields = computed<FieldConfig[]>(() =>
  currentFields.value.filter(f => f.controlType === 'select')
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

/**
 * 可选的目标集合列表（排除当前页面）
 */
const availableTargetCollections = computed(() => {
  return pageConfigs.value
    .filter(p => p.id !== currentPageId.value)
    .map(p => ({
      id: p.id.replace('page-', ''),
      name: p.name
    }))
})

/**
 * 目标集合的字段列表
 */
const targetCollectionFields = computed<FieldConfig[]>(() => {
  if (!deleteBindingTargetCollection.value) return []
  const targetPageId = `page-${deleteBindingTargetCollection.value}`
  const targetConfig = pageConfigStore.getPageConfigById(targetPageId)
  return targetConfig?.fields || []
})

// ==================== 方法 ====================

/**
 * 根据 pageId 加载表单数据
 *
 * 由 watch(currentPageId) 触发，currentPageId 由左侧 PageConfigList 的 v-model 驱动。
 */
function loadFormForPage(id: string): void {
  const config = pageConfigStore.getPageConfigById(id)
  if (!config) return
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
    viewConfig: config.viewConfig || {},
  }
  // Load kanban config
  const vc = config.viewConfig || {}
  kanbanDefaultView.value = vc.defaultView || 'table'
  kanbanGroupField.value = vc.kanban?.groupField || ''
  kanbanCardTitle.value = vc.kanban?.cardTitle || ''
  kanbanCardFields.value = vc.kanban?.cardFields || []
  kanbanColorField.value = vc.kanban?.cardColorField || ''
  // Load delete binding config
  const db = config.deleteBinding
  deleteBindingEnabled.value = db?.enabled || false
  deleteBindingTargetCollection.value = db?.targetCollection || ''
  deleteBindingDialogTitle.value = db?.dialogTitle || ''
  deleteBindingDialogWidth.value = db?.dialogWidth || '500px'
  deleteBindingAutoFillOperator.value = db?.autoFillOperator ?? true
  deleteBindingInheritFields.value = db?.inheritFields ? [...db.inheritFields] : []
}

watch(currentPageId, (id) => {
  if (id) loadFormForPage(id)
})

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
    // 选中新创建的页面（watch 会自动 setup 表单）
    currentPageId.value = created.id
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

  // Build viewConfig from kanban state
  const viewConfig: Record<string, any> = {
    defaultView: kanbanDefaultView.value,
  }
  if (kanbanGroupField.value) {
    viewConfig.kanban = {
      groupField: kanbanGroupField.value,
      cardTitle: kanbanCardTitle.value,
      cardFields: kanbanCardFields.value,
      cardColorField: kanbanColorField.value || undefined,
    }
  }

  // Build deleteBinding config
  let deleteBinding: DeleteBindingConfig | undefined
  if (deleteBindingEnabled.value && deleteBindingTargetCollection.value) {
    deleteBinding = {
      enabled: true,
      targetCollection: deleteBindingTargetCollection.value,
      dialogTitle: deleteBindingDialogTitle.value || undefined,
      dialogWidth: deleteBindingDialogWidth.value || '500px',
      autoFillOperator: deleteBindingAutoFillOperator.value,
      inheritFields: deleteBindingInheritFields.value,
      fields: [],
    }
  }

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
      validationScript: formData.value.validationScript || undefined,
      viewConfig,
      deleteBinding,
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
 * 添加继承字段映射
 */
function addInheritField(): void {
  deleteBindingInheritFields.value.push({
    sourceField: '',
    targetField: ''
  })
}

/**
 * 删除继承字段映射
 */
function removeInheritField(index: number): void {
  deleteBindingInheritFields.value.splice(index, 1)
}

/**
 * 处理保存删除绑定配置
 */
async function handleSaveDeleteBinding(): Promise<void> {
  if (!currentPageId.value) return

  // Build deleteBinding config
  let deleteBinding: DeleteBindingConfig | undefined
  if (deleteBindingEnabled.value && deleteBindingTargetCollection.value) {
    deleteBinding = {
      enabled: true,
      targetCollection: deleteBindingTargetCollection.value,
      dialogTitle: deleteBindingDialogTitle.value || undefined,
      dialogWidth: deleteBindingDialogWidth.value || '500px',
      autoFillOperator: deleteBindingAutoFillOperator.value,
      inheritFields: deleteBindingInheritFields.value,
      fields: [], // 不再需要单独配置，表单字段自动从目标集合获取
    }
  }

  saveLoading.value = true
  try {
    // 获取当前页面配置
    const currentConfig = currentPageConfig.value
    if (!currentConfig) return

    await pageConfigStore.updatePageConfig(currentPageId.value, {
      name: currentConfig.name,
      description: currentConfig.description || '',
      apiEndpoint: currentConfig.apiEndpoint,
      exportScripts: currentConfig.exportScripts || [],
      rowExportScripts: currentConfig.rowExportScripts || [],
      apiPublic: currentConfig.apiPublic || false,
      apiWritable: currentConfig.apiWritable || false,
      validationScript: currentConfig.validationScript || undefined,
      viewConfig: currentConfig.viewConfig || {},
      deleteBinding,
      fields: currentConfig.fields
    })
    ElMessage.success('删除绑定配置已保存')
  } catch (error) {
    ElMessage.error('保存失败')
  } finally {
    saveLoading.value = false
  }
}

/**
 * 处理导航到目标页面配置
 */
function handleNavigateToPage(targetPageId: string): void {
  const targetConfig = pageConfigStore.getPageConfigById(targetPageId)

  if (targetConfig) {
    currentPageId.value = targetPageId
  } else {
    ElMessage.warning('目标页面配置不存在')
  }
}

// ==================== 生命周期 ====================

onMounted(async () => {
  if (pageConfigStore.pageConfigs.length === 0) {
    await pageConfigStore.fetchPageConfigs()
  }
  // 确保菜单数据已加载（左侧列表的项目反查依赖此数据）
  if (menuStore.menuList.length === 0) {
    try {
      await menuStore.fetchMenus()
    } catch {
      // 菜单加载失败时左侧仍能展示，只是项目分组为空
    }
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

.list-card {
  :deep(.el-card__body) {
    padding: 12px;
    display: flex;
    flex-direction: column;
  }
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.page-detail {
  .page-form {
    max-width: 600px;
  }

  :deep(.el-tabs__content) {
    padding: 16px 8px;
  }
}

.inherit-fields-config {
  margin-bottom: 16px;

  .inherit-field-item {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
    padding: 8px;
    background-color: #f5f7fa;
    border-radius: 4px;

    .arrow-icon {
      color: #909399;
    }
  }
}
</style>
