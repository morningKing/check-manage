/**
 * 动态数据页面
 *
 * 职责：
 * - 根据页面配置动态渲染数据页面
 * - 集成数据表格和表单
 * - 实现数据的增删改查功能
 *
 * 核心功能：
 * - 从路由参数获取 pageId
 * - 从 Store 获取页面配置和字段配置
 * - 动态渲染数据表格
 * - 提供新增/编辑对话框
 */
<template>
  <div class="dynamic-page" v-loading="pageLoading">
    <!-- 页面标题和操作栏 -->
    <div class="page-header">
      <div class="page-title">
        <h2>{{ pageConfig?.name || '数据页面' }}</h2>
        <span v-if="pageConfig?.description" class="page-description">
          {{ pageConfig.description }}
        </span>
      </div>
      <div class="page-actions">
        <el-button type="primary" @click="handleAdd">
          <el-icon><Plus /></el-icon>
          新增
        </el-button>
        <el-dropdown @command="handleImportCommand" class="import-dropdown">
          <el-button type="success">
            <el-icon><Upload /></el-icon>
            导入<el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="template">下载导入模板</el-dropdown-item>
              <el-dropdown-item command="import">导入数据</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-dropdown
          v-if="boundExportScripts.length > 0"
          split-button
          type="warning"
          @click="handleExport"
          @command="handleExportCommand"
        >
          <el-icon><Download /></el-icon> 导出
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="excel">导出 Excel</el-dropdown-item>
              <el-dropdown-item divided v-for="s in boundExportScripts" :key="s.id" :command="s.id">
                {{ s.name }} ({{ s.outputFormat }})
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button v-else type="warning" @click="handleExport">
          <el-icon><Download /></el-icon>
          导出
        </el-button>
        <el-button @click="handleRefresh">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button
          v-if="isAdmin"
          type="danger"
          :disabled="selectedRows.length === 0"
          @click="handleBatchDeleteConfirm"
        >
          <el-icon><Delete /></el-icon>
          批量删除{{ selectedRows.length > 0 ? ` (${selectedRows.length})` : '' }}
        </el-button>
      </div>
    </div>

    <!-- 搜索栏 -->
    <div class="search-bar">
      <el-input
        v-model="searchKeyword"
        placeholder="输入关键字搜索..."
        clearable
        :prefix-icon="Search"
        style="width: 300px"
      />
      <span class="search-result-count">
        共 {{ filteredData.length }} 条记录
      </span>
    </div>

    <!-- 数据表格 -->
    <el-card class="table-card">
      <DataTable
        ref="dataTableRef"
        :data="filteredData"
        :fields="effectiveFields"
        :loading="tableLoading"
        :total="filteredData.length"
        :show-pagination="false"
        show-selection
        @edit="handleEdit"
        @delete="handleDeleteConfirm"
        @reference-click="handleReferenceClick"
        @selection-change="handleSelectionChange"
      >
        <template v-if="boundRowExportScripts.length > 0" #extra-actions="{ row }">
          <el-dropdown
            v-if="boundRowExportScripts.length > 1"
            @command="(cmd: string) => handleRowExport(cmd, row)"
            trigger="click"
          >
            <el-button type="warning" link>
              导出<el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  v-for="s in boundRowExportScripts"
                  :key="s.id"
                  :command="s.id"
                >
                  {{ s.name }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button
            v-else
            type="warning"
            link
            @click="handleRowExport(boundRowExportScripts[0].id, row)"
          >
            导出
          </el-button>
        </template>
      </DataTable>
    </el-card>

    <!-- 新增/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="600px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <DynamicForm
        ref="dynamicFormRef"
        :fields="pageFields"
        :initial-data="currentRecord"
        :show-actions="false"
        @submit="handleSubmit"
      />
      <template #footer>
        <el-button @click="dialogVisible = false" :disabled="submitLoading">
          取消
        </el-button>
        <el-button
          type="primary"
          @click="handleFormSubmit"
          :loading="submitLoading"
        >
          确定
        </el-button>
      </template>
    </el-dialog>

    <!-- 删除确认对话框 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      :message="`确定要删除这条记录吗？删除后无法恢复。`"
      type="danger"
      confirm-text="删除"
      @confirm="handleDelete"
    />

    <!-- 批量删除确认对话框 -->
    <ConfirmDialog
      v-model="batchDeleteDialogVisible"
      title="批量删除确认"
      :message="`确定要删除选中的 ${selectedRows.length} 条记录吗？删除后无法恢复。`"
      type="danger"
      confirm-text="全部删除"
      @confirm="handleBatchDelete"
    />

    <!-- 隐藏的文件选择器 -->
    <input
      ref="fileInputRef"
      type="file"
      accept=".xlsx,.xls"
      style="display: none"
      @change="handleFileSelected"
    />

    <!-- 导入进度对话框 -->
    <el-dialog
      v-model="importDialogVisible"
      title="导入数据"
      width="450px"
      :close-on-click-modal="false"
      :close-on-press-escape="!importLoading"
      :show-close="!importLoading"
    >
      <div v-if="importLoading" class="import-progress">
        <el-progress :percentage="importProgress" :stroke-width="20" striped striped-flow />
        <p>正在导入... {{ importCurrent }} / {{ importTotal }}</p>
      </div>
      <div v-else-if="importResult" class="import-result">
        <el-result
          :icon="importResult.failed === 0 ? 'success' : 'warning'"
          :title="importResult.failed === 0 ? '导入完成' : '导入完成（部分失败）'"
        >
          <template #sub-title>
            <p>成功：{{ importResult.success }} 条，失败：{{ importResult.failed }} 条</p>
          </template>
        </el-result>
      </div>
      <template #footer>
        <el-button
          v-if="!importLoading"
          type="primary"
          @click="importDialogVisible = false"
        >
          确定
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * DynamicPage 组件脚本
 *
 * 路由参数：
 * - pageId: 页面配置ID
 *
 * 功能：
 * 1. 加载页面配置和数据
 * 2. 渲染数据表格
 * 3. 处理新增/编辑/删除操作
 */
import { ref, computed, watch, nextTick, onActivated } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, Upload, Download, ArrowDown, Search, Delete } from '@element-plus/icons-vue'
import { usePageConfigStore, useMenuStore, useAuthStore } from '@/stores'
import { DataTable, ConfirmDialog } from '@/components/common'
import { DynamicForm } from '@/components/dynamic-form'
import { exportToExcel, generateImportTemplate, parseImportFile } from '@/utils/excel'
import { withBatch } from '@/utils/batch'
import { getExportScripts, executeExportScript } from '@/api/exportScript'
import type { PageConfig, FieldConfig, DynamicRecord, ExportScript } from '@/types'

// ==================== Props ====================

const props = defineProps<{
  pageId?: string
}>()

// ==================== Route & Store ====================

const route = useRoute()
const router = useRouter()
const pageConfigStore = usePageConfigStore()
const menuStore = useMenuStore()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

// ==================== Refs ====================

/**
 * 动态表单引用
 */
const dynamicFormRef = ref<InstanceType<typeof DynamicForm>>()

/**
 * 数据表格引用
 */
const dataTableRef = ref<InstanceType<typeof DataTable>>()

/**
 * 文件选择器引用
 */
const fileInputRef = ref<HTMLInputElement>()

// ==================== State ====================

/**
 * 页面加载状态
 */
const pageLoading = ref(false)

/**
 * 表格加载状态
 */
const tableLoading = ref(false)

/**
 * 提交加载状态
 */
const submitLoading = ref(false)

/**
 * 表格数据
 */
const tableData = ref<DynamicRecord[]>([])

/**
 * 搜索关键字
 */
const searchKeyword = ref('')

/**
 * 对话框可见性
 */
const dialogVisible = ref(false)

/**
 * 删除对话框可见性
 */
const deleteDialogVisible = ref(false)

/**
 * 当前编辑的记录
 */
const currentRecord = ref<Record<string, any>>({})

/**
 * 待删除的记录ID
 */
const deleteRecordId = ref<string>('')

/**
 * 批量删除对话框可见性
 */
const batchDeleteDialogVisible = ref(false)

/**
 * 当前选中的行
 */
const selectedRows = ref<DynamicRecord[]>([])

/**
 * 是否编辑模式
 */
const isEditMode = ref(false)

/**
 * 导入对话框可见性
 */
const importDialogVisible = ref(false)

/**
 * 导入加载状态
 */
const importLoading = ref(false)

/**
 * 导入进度
 */
const importProgress = ref(0)
const importCurrent = ref(0)
const importTotal = ref(0)

/**
 * 导入结果
 */
const importResult = ref<{ success: number; failed: number } | null>(null)

/**
 * 所有导出脚本（缓存）
 */
const allExportScripts = ref<ExportScript[]>([])

// ==================== 计算属性 ====================

/**
 * 当前页面ID
 */
const pageId = computed(() => props.pageId || (route.params.pageId as string))

/**
 * 页面配置
 */
const pageConfig = computed<PageConfig | undefined>(() => {
  return pageConfigStore.getPageConfigById(pageId.value)
})

/**
 * 页面字段配置
 */
const pageFields = computed<FieldConfig[]>(() => {
  return pageConfigStore.getPageFields(pageId.value)
})

/**
 * 当前页面绑定的导出脚本
 */
const boundExportScripts = computed<ExportScript[]>(() => {
  const ids = pageConfig.value?.exportScripts || []
  if (ids.length === 0) return []
  return allExportScripts.value.filter(s => ids.includes(s.id))
})

/**
 * 当前页面绑定的行级导出脚本
 */
const boundRowExportScripts = computed<ExportScript[]>(() => {
  const ids = pageConfig.value?.rowExportScripts || []
  if (ids.length === 0) return []
  return allExportScripts.value.filter(s => ids.includes(s.id))
})

/**
 * 有效字段列表（含引用字段展开的继承虚拟列）
 * 用于 DataTable 显示，在每个 reference 字段后插入继承字段列
 */
const effectiveFields = computed<FieldConfig[]>(() => {
  const result: FieldConfig[] = []
  for (const field of pageFields.value) {
    result.push(field)
    // 对 reference 字段，展开继承字段为虚拟列
    if (field.controlType === 'reference' && field.referenceConfig?.inheritFields?.length) {
      const config = field.referenceConfig
      // 获取目标集合的字段配置，用于取 label 和 controlType
      const targetPageConfig = pageConfigStore.getPageConfigById(`page-${config.targetCollection}`)
      const targetFields = targetPageConfig?.fields || []
      for (const inheritFieldName of config.inheritFields) {
        const parentField = targetFields.find((f) => f.fieldName === inheritFieldName)
        result.push({
          id: `_ref_${field.fieldName}_${inheritFieldName}`,
          fieldName: `_ref_${field.fieldName}_${inheritFieldName}`,
          label: parentField?.label || inheritFieldName,
          controlType: parentField?.controlType || 'text',
          required: false,
          order: field.order + 0.1,
          hidden: false,
          disabled: true,
          options: parentField?.options
        })
      }
    }
  }
  return result
})

/**
 * 对话框标题
 */
const dialogTitle = computed(() => {
  return isEditMode.value ? '编辑记录' : '新增记录'
})

/**
 * 按关键字过滤后的表格数据
 */
const filteredData = computed<DynamicRecord[]>(() => {
  const keyword = searchKeyword.value.trim().toLowerCase()
  if (!keyword) return tableData.value

  return tableData.value.filter(record => {
    for (const field of pageFields.value) {
      const val = record[field.fieldName]
      if (val === null || val === undefined) continue

      if (['text', 'textarea', 'number'].includes(field.controlType)) {
        if (String(val).toLowerCase().includes(keyword)) return true
      }

      if (['select', 'radio'].includes(field.controlType)) {
        const opt = field.options?.find(o => o.value === val)
        const label = opt?.label || String(val)
        if (label.toLowerCase().includes(keyword)) return true
      }

      if (['multiSelect', 'checkbox'].includes(field.controlType)) {
        if (Array.isArray(val)) {
          const matched = val.some(v => {
            const opt = field.options?.find(o => o.value === v)
            return (opt?.label || String(v)).toLowerCase().includes(keyword)
          })
          if (matched) return true
        }
      }

      if (['date', 'datetime'].includes(field.controlType)) {
        if (String(val).toLowerCase().includes(keyword)) return true
      }

      if (field.controlType === 'reference') {
        const displayVal = record[`_ref_${field.fieldName}_display`]
        if (displayVal && String(displayVal).toLowerCase().includes(keyword)) return true
        if (field.referenceConfig?.inheritFields) {
          for (const inh of field.referenceConfig.inheritFields) {
            const refVal = record[`_ref_${field.fieldName}_${inh}`]
            if (refVal && String(refVal).toLowerCase().includes(keyword)) return true
          }
        }
      }
    }
    return false
  })
})

// ==================== 方法 ====================

/**
 * 加载页面数据
 */
async function loadPageData(): Promise<void> {
  if (!pageId.value) return

  tableLoading.value = true
  try {
    const data = await pageConfigStore.fetchPageData(pageId.value)
    tableData.value = data
    // 如果有 recordId query 参数，高亮定位到该记录
    const recordId = route.query.recordId as string
    if (recordId) {
      highlightRecord(recordId)
    }
  } catch (error) {
    console.error('加载数据失败:', error)
    ElMessage.error('加载数据失败')
  } finally {
    tableLoading.value = false
  }
}

/**
 * 处理新增
 */
function handleAdd(): void {
  isEditMode.value = false
  currentRecord.value = {}
  dialogVisible.value = true
}

/**
 * 处理编辑
 */
function handleEdit(row: DynamicRecord): void {
  isEditMode.value = true
  currentRecord.value = { ...row }
  dialogVisible.value = true
}

/**
 * 处理删除确认
 */
function handleDeleteConfirm(row: DynamicRecord): void {
  deleteRecordId.value = row.id
  deleteDialogVisible.value = true
}

/**
 * 处理删除
 */
async function handleDelete(): Promise<void> {
  if (!deleteRecordId.value) return

  try {
    await pageConfigStore.deletePageData(pageId.value, deleteRecordId.value)
    ElMessage.success('删除成功')
    deleteDialogVisible.value = false
    // 刷新数据
    await loadPageData()
  } catch (error) {
    ElMessage.error('删除失败')
  }
}

/**
 * 处理多选变化
 */
function handleSelectionChange(rows: DynamicRecord[]): void {
  selectedRows.value = rows
}

/**
 * 处理批量删除确认
 */
function handleBatchDeleteConfirm(): void {
  if (selectedRows.value.length === 0) return
  batchDeleteDialogVisible.value = true
}

/**
 * 执行批量删除
 */
async function handleBatchDelete(): Promise<void> {
  const rows = [...selectedRows.value]
  batchDeleteDialogVisible.value = false

  let success = 0
  let failed = 0

  await withBatch(`批量删除 ${rows.length} 条${pageConfig.value?.name || '数据'}`, async () => {
    for (const row of rows) {
      try {
        await pageConfigStore.deletePageData(pageId.value, row.id)
        success++
      } catch {
        failed++
      }
    }
  })

  selectedRows.value = []
  dataTableRef.value?.clearSelection()

  if (failed === 0) {
    ElMessage.success(`成功删除 ${success} 条记录`)
  } else {
    ElMessage.warning(`删除完成：成功 ${success} 条，失败 ${failed} 条`)
  }

  await loadPageData()
}

/**
 * 处理表单提交（从表单组件触发）
 */
async function handleSubmit(data: Record<string, any>): Promise<void> {
  await submitFormData(data)
}

/**
 * 处理表单提交按钮点击
 */
async function handleFormSubmit(): Promise<void> {
  // 验证表单
  const isValid = await dynamicFormRef.value?.validate()
  if (!isValid) return

  const formData = dynamicFormRef.value?.getFormData()
  if (formData) {
    await submitFormData(formData)
  }
}

/**
 * 提交表单数据
 */
async function submitFormData(data: Record<string, any>): Promise<void> {
  submitLoading.value = true
  try {
    const hasRelations = pageFields.value.some(f => f.controlType === 'relation')

    const doSave = async () => {
      // 分离关联字段和普通字段
      const regularData = pageConfigStore.stripRelationFields(pageId.value, data)

      if (isEditMode.value) {
        // 编辑模式
        await pageConfigStore.updatePageData(pageId.value, currentRecord.value.id, regularData)
        await pageConfigStore.saveRelations(pageId.value, currentRecord.value.id, data)
        ElMessage.success('更新成功')
      } else {
        // 新增模式：先创建记录获取ID，再保存关联
        const created = await pageConfigStore.addPageData(pageId.value, regularData)
        await pageConfigStore.saveRelations(pageId.value, created.id, data)
        ElMessage.success('新增成功')
      }
    }

    if (hasRelations) {
      const name = data[pageFields.value[0]?.fieldName] || ''
      const actionLabel = isEditMode.value ? '修改' : '新增'
      await withBatch(`${actionLabel}${pageConfig.value?.name || '数据'}「${name}」`, doSave)
    } else {
      await doSave()
    }

    dialogVisible.value = false
    // 刷新数据
    await loadPageData()
  } catch (error: any) {
    const resp = error.response?.data
    if (resp?.validationErrors?.length) {
      ElMessage.error(resp.validationErrors.join('；'))
      if (resp.validationWarnings?.length) {
        ElMessage.warning(resp.validationWarnings.join('；'))
      }
    } else if (resp?.error) {
      ElMessage.error(resp.error)
    } else {
      ElMessage.error(isEditMode.value ? '更新失败' : '新增失败')
    }
  } finally {
    submitLoading.value = false
  }
}

/**
 * 处理导出
 */
async function handleExport(): Promise<void> {
  if (tableData.value.length === 0) {
    ElMessage.warning('暂无数据可导出')
    return
  }
  const name = pageConfig.value?.name || '数据'
  const relationDisplayMap = await pageConfigStore.fetchRelationDisplayMaps(pageId.value)
  exportToExcel(tableData.value, effectiveFields.value, name, relationDisplayMap)
  ElMessage.success('导出成功')
}

/**
 * 处理导出下拉命令（自定义脚本导出）
 */
async function handleExportCommand(command: string): Promise<void> {
  if (command === 'excel') {
    handleExport()
    return
  }
  // command is a script id
  if (tableData.value.length === 0) {
    ElMessage.warning('暂无数据可导出')
    return
  }
  const collection = pageId.value.replace('page-', '')
  if (!collection) {
    ElMessage.error('无法确定数据集合')
    return
  }
  try {
    await executeExportScript(command, collection)
    ElMessage.success('导出成功')
  } catch {
    ElMessage.error('导出失败')
  }
}

/**
 * 处理行级导出
 */
async function handleRowExport(scriptId: string, row: DynamicRecord): Promise<void> {
  const collection = pageId.value.replace('page-', '')
  if (!collection) {
    ElMessage.error('无法确定数据集合')
    return
  }
  try {
    await executeExportScript(scriptId, collection, row.id)
    ElMessage.success('导出成功')
  } catch {
    ElMessage.error('导出失败')
  }
}

/**
 * 处理导入下拉命令
 */
function handleImportCommand(command: string): void {
  if (command === 'template') {
    handleDownloadTemplate()
  } else if (command === 'import') {
    fileInputRef.value?.click()
  }
}

/**
 * 下载导入模板
 */
function handleDownloadTemplate(): void {
  const name = pageConfig.value?.name || '数据'
  generateImportTemplate(pageFields.value, `${name}-导入模板`)
  ElMessage.success('模板已下载')
}

/**
 * 处理文件选择
 */
async function handleFileSelected(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  // 重置 input 以便下次选同一文件仍然触发
  input.value = ''

  try {
    const records = await parseImportFile(file, pageFields.value)
    if (records.length === 0) {
      ElMessage.warning('文件中没有可导入的数据')
      return
    }
    await doImport(records)
  } catch (error) {
    ElMessage.error('文件解析失败，请检查文件格式')
  }
}

/**
 * 执行批量导入
 */
async function doImport(records: Record<string, any>[]): Promise<void> {
  importResult.value = null
  importLoading.value = true
  importProgress.value = 0
  importCurrent.value = 0
  importTotal.value = records.length
  importDialogVisible.value = true

  // 将关联字段的显示名称解析为记录 ID
  await pageConfigStore.resolveRelationImportValues(pageId.value, records)

  let success = 0
  let failed = 0

  await withBatch(`导入 ${records.length} 条${pageConfig.value?.name || '数据'}`, async () => {
    for (let i = 0; i < records.length; i++) {
      try {
        const regularData = pageConfigStore.stripRelationFields(pageId.value, records[i])
        const created = await pageConfigStore.addPageData(pageId.value, regularData)
        // 保存关联关系数据
        await pageConfigStore.saveRelations(pageId.value, created.id, records[i])
        success++
      } catch {
        failed++
      }
      importCurrent.value = i + 1
      importProgress.value = Math.round(((i + 1) / records.length) * 100)
    }
  })

  importLoading.value = false
  importResult.value = { success, failed }

  if (success > 0) {
    await loadPageData()
  }
}

/**
 * 处理刷新
 */
async function handleRefresh(): Promise<void> {
  await loadPageData()
  ElMessage.success('数据已刷新')
}

/**
 * 处理引用字段点击 — 跳转到被引用数据所在页面
 */
function handleReferenceClick(row: DynamicRecord, field: FieldConfig): void {
  const targetCollection = field.referenceConfig?.targetCollection
  if (!targetCollection) return

  const targetPageId = `page-${targetCollection}`
  const targetMenu = menuStore.menuList.find(m => m.pageId === targetPageId)
  if (!targetMenu?.path) {
    ElMessage.warning('未找到被引用数据的页面')
    return
  }

  const recordId = row[field.fieldName]
  router.push({ path: targetMenu.path, query: { recordId } })
}

/**
 * 高亮定位指定记录（从引用跳转过来时调用）
 */
async function highlightRecord(recordId: string): Promise<void> {
  await nextTick()
  const row = tableData.value.find(r => r.id === recordId)
  if (!row) return

  // 通过 el-table 高亮该行
  const elTable = dataTableRef.value?.tableRef
  if (elTable) {
    elTable.setCurrentRow(row)
  }

  // 滚动到该行并闪烁提示
  await nextTick()
  const tableEl = dataTableRef.value?.$el as HTMLElement | undefined
  if (tableEl) {
    const currentRow = tableEl.querySelector('.el-table__body tr.current-row') as HTMLElement
    if (currentRow) {
      currentRow.scrollIntoView({ behavior: 'smooth', block: 'center' })
      currentRow.classList.add('highlight-flash')
      setTimeout(() => currentRow.classList.remove('highlight-flash'), 2000)
    }
  }

  // 清除 query 参数，避免刷新时重复定位
  router.replace({ query: {} })
}

// ==================== 监听 ====================

/**
 * 监听页面ID变化，重新加载数据
 */
watch(
  () => pageId.value,
  (newPageId) => {
    if (newPageId) {
      loadPageData()
    }
  },
  { immediate: true }
)

// ==================== 生命周期 ====================

onActivated(async () => {
  // 重新加载页面数据（keep-alive 缓存可能显示过时数据，如双向关联同步后）
  await loadPageData()
  // 加载导出脚本列表（用于绑定展示）
  try {
    allExportScripts.value = await getExportScripts()
  } catch {
    // 非管理员可能无权访问，忽略错误
  }
})
</script>

<style scoped lang="scss">
.dynamic-page {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;

  .page-title {
    h2 {
      margin: 0;
      font-size: 20px;
      font-weight: 600;
      color: #303133;
    }

    .page-description {
      display: block;
      margin-top: 4px;
      font-size: 14px;
      color: #909399;
    }
  }

  .page-actions {
    display: flex;
    gap: 8px;
    align-items: center;
  }
}

.import-progress {
  text-align: center;
  padding: 20px 0;

  p {
    margin-top: 12px;
    color: #606266;
  }
}

.import-result {
  padding: 10px 0;
}

.search-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;

  .search-result-count {
    color: #909399;
    font-size: 13px;
  }
}

.table-card {
  flex: 1;
  overflow: hidden;

  :deep(.el-card__body) {
    height: 100%;
    padding: 16px;
    overflow: auto;
  }

  :deep(.highlight-flash) {
    animation: row-flash 0.6s ease-in-out 3;
  }
}

@keyframes row-flash {
  0%, 100% { background-color: transparent; }
  50% { background-color: #ecf5ff; }
}
</style>
