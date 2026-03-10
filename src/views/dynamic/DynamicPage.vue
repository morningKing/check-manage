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
        <el-button v-if="!isGuest" type="primary" @click="handleAdd">
          <el-icon><Plus /></el-icon>
          新增
        </el-button>
        <el-dropdown v-if="!isGuest" @command="handleImportCommand" class="import-dropdown">
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
          @click="diffDialogVisible = true"
        >
          <el-icon><DCaret /></el-icon>
          数据对比
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
      <template v-if="!queryMode">
        <el-input
          v-model="searchKeyword"
          placeholder="输入关键字搜索..."
          clearable
          :prefix-icon="Search"
          style="width: 300px"
        />
      </template>
      <template v-else>
        <el-input
          v-model="mongoQueryText"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 4 }"
          placeholder='{"field": {"$regex": "value"}, "age": {"$gte": 18}}'
          style="flex: 1; max-width: 600px; font-family: monospace"
          @keydown.ctrl.enter="executeMongoQuery"
        />
        <el-button type="primary" :loading="tableLoading" @click="executeMongoQuery">
          查询
        </el-button>
        <el-button v-if="activeMongoQuery" @click="clearMongoQuery">
          清除
        </el-button>
        <el-popover placement="bottom-start" :width="420" trigger="click">
          <template #reference>
            <el-button text type="info">语法帮助</el-button>
          </template>
          <div class="query-help">
            <p><b>MongoDB 查询语法</b>（支持中文字段名）</p>
            <table>
              <tr><td><code>{"用例ID": "IC-001"}</code></td><td>精确匹配</td></tr>
              <tr><td><code>{"名称": {"$regex": "test"}}</code></td><td>正则匹配（不区分大小写）</td></tr>
              <tr><td><code>{"名称": {"$like": "test"}}</code></td><td>模糊匹配（%test%）</td></tr>
              <tr><td><code>{"age": {"$gt": 18}}</code></td><td>大于（$gte, $lt, $lte 类似）</td></tr>
              <tr><td><code>{"状态": {"$in": ["a","b"]}}</code></td><td>在列表中</td></tr>
              <tr><td><code>{"状态": {"$nin": ["a"]}}</code></td><td>不在列表中</td></tr>
              <tr><td><code>{"field": {"$ne": "val"}}</code></td><td>不等于</td></tr>
              <tr><td><code>{"field": {"$exists": true}}</code></td><td>字段存在/不存在</td></tr>
              <tr><td><code>{"$or": [{...}, {...}]}</code></td><td>逻辑或</td></tr>
              <tr><td><code>{"$and": [{...}, {...}]}</code></td><td>逻辑与</td></tr>
            </table>
            <p style="margin-top:8px;color:#909399;font-size:12px">Ctrl+Enter 快捷执行 · 字段名可用中文标签或英文字段名</p>
          </div>
        </el-popover>
      </template>
      <el-tooltip :content="queryMode ? '简单搜索' : '高级查询'">
        <el-button
          :type="queryMode ? 'primary' : 'default'"
          :icon="queryMode ? Search : DCaret"
          circle
          @click="toggleQueryMode"
        />
      </el-tooltip>
      <span class="search-result-count">
        共 {{ filteredData.length }} 条记录
        <template v-if="activeMongoQuery">
          （已筛选）
        </template>
      </span>
    </div>

    <!-- 数据表格 -->
    <el-card class="table-card">
      <DataTable
        ref="dataTableRef"
        :data="paginatedData"
        :fields="effectiveFields"
        :loading="tableLoading"
        :total="filteredData.length"
        :show-pagination="true"
        :show-actions="!isGuest"
        show-selection
        @view="handleView"
        @edit="handleEdit"
        @delete="handleDeleteConfirm"
        @reference-click="handleReferenceClick"
        @relation-click="handleRelationClick"
        @quote-click="handleQuoteClick"
        @selection-change="handleSelectionChange"
        @page-change="handlePageChange"
        @filter-change="handleFilterChange"
      >
        <template #extra-actions="{ row }">
          <el-button type="info" link @click="handleShowRelationGraph(row)">
            图谱
          </el-button>
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
            v-else-if="boundRowExportScripts.length === 1"
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

    <!-- 查看记录对话框 -->
    <el-dialog
      v-model="viewDialogVisible"
      title="查看记录"
      width="700px"
      destroy-on-close
    >
      <el-descriptions :column="1" border>
        <el-descriptions-item
          v-for="field in viewDisplayFields"
          :key="field.id"
          :label="field.label"
          :label-width="140"
        >
          <!-- 关联关系字段：Tag 可点击跳转 -->
          <template v-if="field.controlType === 'relation'">
            <span v-if="!viewRecord[`_rel_${field.fieldName}_labels`]?.length">-</span>
            <span v-else class="relation-tags">
              <el-tag
                v-for="item in viewRecord[`_rel_${field.fieldName}_labels`]"
                :key="item.id"
                size="small"
                class="relation-tag-link"
                @click="viewDialogVisible = false; handleRelationClick(item.id, field)"
              >{{ item.label }}</el-tag>
            </span>
          </template>

          <!-- 引用选择字段：Tag 可点击跳转 -->
          <template v-else-if="field.controlType === 'quoteSelect'">
            <span v-if="!viewRecord[`_quote_${field.fieldName}_labels`]?.length">-</span>
            <span v-else class="relation-tags">
              <el-tag
                v-for="item in viewRecord[`_quote_${field.fieldName}_labels`]"
                :key="item.id"
                size="small"
                class="relation-tag-link"
                @click="viewDialogVisible = false; handleQuoteClick(item.id, field)"
              >{{ item.label }}</el-tag>
            </span>
          </template>

          <!-- 数据引用字段：可点击跳转 -->
          <template v-else-if="field.controlType === 'reference'">
            <span v-if="!viewRecord[field.fieldName]">-</span>
            <span
              v-else
              class="reference-link"
              @click="viewDialogVisible = false; handleReferenceClick(viewRecord as DynamicRecord, field)"
            >{{ viewRecord[`_ref_${field.fieldName}_display`] || viewRecord[field.fieldName] }}</span>
          </template>

          <!-- 选项类字段：显示标签 -->
          <template v-else-if="['select', 'radio'].includes(field.controlType)">
            {{ formatViewValue(field) }}
          </template>

          <!-- 多选类字段：Tag 展示 -->
          <template v-else-if="['multiSelect', 'checkbox'].includes(field.controlType)">
            <span v-if="!Array.isArray(viewRecord[field.fieldName]) || viewRecord[field.fieldName].length === 0">-</span>
            <span v-else class="relation-tags">
              <el-tag
                v-for="v in viewRecord[field.fieldName]"
                :key="v"
                size="small"
              >{{ field.options?.find(o => o.value === v)?.label || v }}</el-tag>
            </span>
          </template>

          <!-- 文件/图片字段 -->
          <template v-else-if="field.controlType === 'file'">
            <span v-if="!Array.isArray(viewRecord[field.fieldName]) || viewRecord[field.fieldName].length === 0">-</span>
            <div v-else>
              <div v-for="(f, idx) in viewRecord[field.fieldName]" :key="idx">
                <el-link type="primary" :href="f.url" target="_blank">{{ f.name }}</el-link>
              </div>
            </div>
          </template>

          <template v-else-if="field.controlType === 'image'">
            <span v-if="!Array.isArray(viewRecord[field.fieldName]) || viewRecord[field.fieldName].length === 0">-</span>
            <div v-else class="view-images">
              <el-image
                v-for="(img, idx) in viewRecord[field.fieldName]"
                :key="idx"
                :src="img.url"
                :preview-src-list="viewRecord[field.fieldName].map((i: any) => i.url)"
                :initial-index="idx"
                fit="cover"
                class="view-image-item"
              />
            </div>
          </template>

          <!-- 日期/时间字段 -->
          <template v-else-if="['date', 'datetime', 'autoTimestamp'].includes(field.controlType)">
            {{ formatViewDate(viewRecord[field.fieldName], field.controlType) }}
          </template>

          <!-- 多行文本 -->
          <template v-else-if="field.controlType === 'textarea'">
            <span class="view-textarea">{{ viewRecord[field.fieldName] || '-' }}</span>
          </template>

          <!-- 富文本 -->
          <template v-else-if="field.controlType === 'richText'">
            <div class="view-richtext" v-html="viewRecord[field.fieldName] || '-'" />
          </template>

          <!-- 默认：纯文本 -->
          <template v-else>
            {{ viewRecord[field.fieldName] ?? '-' }}
          </template>
        </el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button @click="viewDialogVisible = false">关闭</el-button>
        <el-button type="info" @click="viewDialogVisible = false; handleShowRelationGraph(viewRecord as DynamicRecord)">
          关系图谱
        </el-button>
        <el-button v-if="!isGuest" type="primary" @click="viewDialogVisible = false; handleEdit(viewRecord as DynamicRecord)">
          编辑
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
      accept=".xlsx,.xls,.json"
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

    <!-- 数据对比对话框 -->
    <BackupDiffDialog
      v-model="diffDialogVisible"
      :collection="collection"
      :page-name="pageConfig?.name || '数据'"
    />

    <!-- 关系图谱对话框 -->
    <RelationGraphDialog
      v-model="graphDialogVisible"
      :collection="collection"
      :record-id="graphRecordId"
      @navigate="handleGraphNodeNavigate"
      @edit="handleGraphNodeEdit"
    />
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
import { Plus, Refresh, Upload, Download, ArrowDown, Search, Delete, DCaret } from '@element-plus/icons-vue'
import { usePageConfigStore, useMenuStore, useAuthStore } from '@/stores'
import { DataTable, ConfirmDialog, BackupDiffDialog, RelationGraphDialog } from '@/components/common'
import { DynamicForm } from '@/components/dynamic-form'
import { exportToExcel, generateImportTemplate, parseImportFile, parseJsonImportFile } from '@/utils/excel'
import { withBatch } from '@/utils/batch'
import { getExportScripts, executeExportScript } from '@/api/exportScript'
import { post } from '@/utils/request'
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
const isGuest = computed(() => authStore.isGuest)

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
 * 列筛选条件
 */
const columnFilters = ref<Record<string, { value: any; value2?: any; operator?: string }>>({})

/**
 * 高级查询模式
 */
const queryMode = ref(false)
const mongoQueryText = ref('')
const activeMongoQuery = ref<Record<string, any> | null>(null)

/**
 * 对话框可见性
 */
const dialogVisible = ref(false)

/**
 * 查看对话框可见性
 */
const viewDialogVisible = ref(false)

/**
 * 删除对话框可见性
 */
const deleteDialogVisible = ref(false)

/**
 * 当前编辑的记录
 */
const currentRecord = ref<Record<string, any>>({})

/**
 * 当前查看的记录
 */
const viewRecord = ref<Record<string, any>>({})

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

/**
 * 数据对比对话框可见性
 */
const diffDialogVisible = ref(false)

/**
 * 关系图谱对话框可见性
 */
const graphDialogVisible = ref(false)

/**
 * 关系图谱记录ID
 */
const graphRecordId = ref('')

/**
 * 分页状态
 */
const currentPage = ref(1)
const currentPageSize = ref(50)

// ==================== 计算属性 ====================

/**
 * 当前页面ID
 */
const pageId = computed(() => props.pageId || (route.params.pageId as string))

/**
 * 集合名称（用于对比接口）
 */
const collection = computed(() => pageId.value.replace('page-', ''))

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
 * 查看对话框中显示的字段列表（含 reference 继承字段，按 order 排序）
 */
const viewDisplayFields = computed<FieldConfig[]>(() => {
  const result: FieldConfig[] = []
  for (const field of pageFields.value) {
    if (field.hidden) continue
    result.push(field)
    if (field.controlType === 'reference' && field.referenceConfig?.inheritFields?.length) {
      const config = field.referenceConfig
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
  return result.sort((a, b) => a.order - b.order)
})

/**
 * 按关键字和列筛选条件过滤后的表格数据
 */
const filteredData = computed<DynamicRecord[]>(() => {
  let result = tableData.value
  const keyword = searchKeyword.value.trim().toLowerCase()

  if (keyword) {
    result = result.filter(record => {
      for (const field of pageFields.value) {
        const val = record[field.fieldName]
        if (val === null || val === undefined) continue

        if (['text', 'textarea', 'number', 'autoSequence'].includes(field.controlType)) {
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

        if (['date', 'datetime', 'autoTimestamp'].includes(field.controlType)) {
          if (String(val).toLowerCase().includes(keyword)) return true
        }

        if (field.controlType === 'relation') {
          const labels = record[`_rel_${field.fieldName}_labels`]
          if (Array.isArray(labels)) {
            const matched = labels.some((item: { id: string; label: string }) =>
              item.label.toLowerCase().includes(keyword)
            )
            if (matched) return true
          }
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

        if (field.controlType === 'quoteSelect') {
          const labels = record[`_quote_${field.fieldName}_labels`]
          if (Array.isArray(labels)) {
            const matched = labels.some((item: { id: string; label: string }) =>
              item.label.toLowerCase().includes(keyword)
            )
            if (matched) return true
          }
        }
      }
      return false
    })
  }

  const filterEntries = Object.entries(columnFilters.value)
  if (filterEntries.length > 0) {
    result = result.filter(record => {
      return filterEntries.every(([fieldName, filter]) => {
        const field = pageFields.value.find(f => f.fieldName === fieldName)
        if (!field) return true

        if (field.controlType === 'relation') {
          const labels = record[`_rel_${fieldName}_labels`]
          if (!Array.isArray(labels) || labels.length === 0) return false
          const keyword = String(filter.value).toLowerCase()
          return labels.some((item: { id: string; label: string }) =>
            item.label.toLowerCase().includes(keyword)
          )
        }

        if (field.controlType === 'quoteSelect') {
          const labels = record[`_quote_${fieldName}_labels`]
          if (!Array.isArray(labels) || labels.length === 0) return false
          const keyword = String(filter.value).toLowerCase()
          return labels.some((item: { id: string; label: string }) =>
            item.label.toLowerCase().includes(keyword)
          )
        }

        if (field.controlType === 'reference') {
          const display = record[`_ref_${fieldName}_display`]
          if (!display) return false
          const keyword = String(filter.value).toLowerCase()
          return String(display).toLowerCase().includes(keyword)
        }

        const val = record[fieldName]
        if (val === null || val === undefined || val === '') return false

        if (['select', 'radio'].includes(field.controlType)) {
          return val === filter.value
        }

        if (['multiSelect', 'checkbox'].includes(field.controlType)) {
          if (!Array.isArray(val) || !Array.isArray(filter.value)) return false
          return filter.value.every(v => val.includes(v))
        }

        if (['date', 'datetime', 'autoTimestamp'].includes(field.controlType)) {
          const recordDate = new Date(val).getTime()
          const filterDate = filter.value ? new Date(filter.value).getTime() : null
          const filterDate2 = filter.value2 ? new Date(filter.value2).getTime() : null

          if (filter.operator === 'eq' && filterDate) {
            return recordDate === filterDate
          } else if (filter.operator === 'lt' && filterDate) {
            return recordDate < filterDate
          } else if (filter.operator === 'gt' && filterDate) {
            return recordDate > filterDate
          } else if (filter.operator === 'between' && filterDate && filterDate2) {
            return recordDate >= filterDate && recordDate <= filterDate2
          }
          return true
        }

        if (field.controlType === 'number') {
          const numVal = Number(val)
          const numFilter = Number(filter.value)
          const numFilter2 = Number(filter.value2)

          if (filter.operator === 'eq') {
            return numVal === numFilter
          } else if (filter.operator === 'gt') {
            return numVal > numFilter
          } else if (filter.operator === 'lt') {
            return numVal < numFilter
          } else if (filter.operator === 'between') {
            return numVal >= numFilter && numVal <= numFilter2
          }
          return true
        }

        const strVal = String(val).toLowerCase()
        const strFilter = String(filter.value).toLowerCase()
        return strVal.includes(strFilter)
      })
    })
  }

  return result
})

/**
 * 分页后的数据（基于 filteredData 切片）
 */
const paginatedData = computed<DynamicRecord[]>(() => {
  const start = (currentPage.value - 1) * currentPageSize.value
  const end = start + currentPageSize.value
  return filteredData.value.slice(start, end)
})

// ==================== 方法 ====================

/**
 * 处理分页变化
 */
function handlePageChange(page: number, pageSize: number): void {
  currentPage.value = page
  currentPageSize.value = pageSize
}

/**
 * 处理列筛选变化
 */
function handleFilterChange(filters: Record<string, { value: any; value2?: any; operator?: string }>): void {
  columnFilters.value = filters
  currentPage.value = 1
}

/**
 * 格式化查看对话框中的选项字段值
 */
function formatViewValue(field: FieldConfig): string {
  const value = viewRecord.value[field.fieldName]
  if (value === null || value === undefined || value === '') return '-'
  const opt = field.options?.find(o => o.value === value)
  return opt?.label || String(value)
}

/**
 * 格式化查看对话框中的日期值
 */
function formatViewDate(value: any, controlType: string): string {
  if (!value) return '-'
  try {
    const date = new Date(value)
    if (isNaN(date.getTime())) return String(value)
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    if (controlType === 'date') return `${y}-${m}-${d}`
    const hh = String(date.getHours()).padStart(2, '0')
    const mm = String(date.getMinutes()).padStart(2, '0')
    const ss = String(date.getSeconds()).padStart(2, '0')
    return `${y}-${m}-${d} ${hh}:${mm}:${ss}`
  } catch {
    return String(value)
  }
}

/**
 * 加载页面数据
 */
async function loadPageData(): Promise<void> {
  if (!pageId.value) return

  tableLoading.value = true
  try {
    const query = activeMongoQuery.value || undefined
    const data = await pageConfigStore.fetchPageData(pageId.value, query)
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
 * 切换查询模式
 */
function toggleQueryMode(): void {
  queryMode.value = !queryMode.value
  if (!queryMode.value && activeMongoQuery.value) {
    clearMongoQuery()
  }
}

/**
 * 执行 MongoDB 查询
 */
async function executeMongoQuery(): Promise<void> {
  const text = mongoQueryText.value.trim()
  if (!text) {
    clearMongoQuery()
    return
  }
  try {
    const query = JSON.parse(text)
    if (typeof query !== 'object' || Array.isArray(query)) {
      ElMessage.warning('查询必须是 JSON 对象，如 {"field": "value"}')
      return
    }
    activeMongoQuery.value = query
    currentPage.value = 1
    await loadPageData()
  } catch (e: any) {
    if (e instanceof SyntaxError) {
      ElMessage.error('JSON 格式错误: ' + e.message)
    } else {
      ElMessage.error(e.message || '查询失败')
    }
  }
}

/**
 * 清除查询
 */
async function clearMongoQuery(): Promise<void> {
  activeMongoQuery.value = null
  mongoQueryText.value = ''
  currentPage.value = 1
  await loadPageData()
}

/**
 * 处理查看记录
 */
function handleView(row: DynamicRecord): void {
  viewRecord.value = { ...row }
  viewDialogVisible.value = true
}

/**
 * 处理新增
 */
function handleAdd(): void {
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }
  isEditMode.value = false
  currentRecord.value = {}
  dialogVisible.value = true
}

/**
 * 处理编辑
 */
function handleEdit(row: DynamicRecord): void {
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }
  isEditMode.value = true
  currentRecord.value = { ...row }
  dialogVisible.value = true
}

/**
 * 处理删除确认
 */
function handleDeleteConfirm(row: DynamicRecord): void {
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }
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
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }
  if (selectedRows.value.length === 0) return
  batchDeleteDialogVisible.value = true
}

/**
 * 执行批量删除
 */
async function handleBatchDelete(): Promise<void> {
  const rows = [...selectedRows.value]
  batchDeleteDialogVisible.value = false

  try {
    const ids = rows.map((r) => r.id)
    const result = await pageConfigStore.batchDeletePageData(pageId.value, ids)

    selectedRows.value = []
    dataTableRef.value?.clearSelection()

    const blockedCount = result.blocked ? Object.keys(result.blocked).length : 0
    if (blockedCount === 0) {
      ElMessage.success(`成功删除 ${result.deleted} 条记录`)
    } else {
      ElMessage.warning(`删除完成：成功 ${result.deleted} 条，${blockedCount} 条因被引用跳过`)
    }

    await loadPageData()
  } catch {
    ElMessage.error('批量删除失败')
  }
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
    let savedRecordId = ''

    const doSave = async () => {
      // 分离关联字段和普通字段
      const regularData = pageConfigStore.stripRelationFields(pageId.value, data)

      if (isEditMode.value) {
        // 编辑模式：关联数据与主数据在同一事务中提交，保证原子性
        await pageConfigStore.updatePageData(pageId.value, currentRecord.value.id, regularData, data)
        savedRecordId = currentRecord.value.id
        ElMessage.success('更新成功')
      } else {
        // 新增模式：关联数据与主数据在同一事务中提交，保证原子性
        const created = await pageConfigStore.addPageData(pageId.value, regularData, undefined, data)
        savedRecordId = created.id
        ElMessage.success('新增成功')
      }
    }

    if (hasRelations) {
      const displayField = pageFields.value.find(f => !['autoTimestamp', 'autoSequence', 'relation'].includes(f.controlType))
      const name = (displayField ? data[displayField.fieldName] : '') || ''
      const actionLabel = isEditMode.value ? '修改' : '新增'
      await withBatch(`${actionLabel}${pageConfig.value?.name || '数据'}「${name}」`, doSave)
    } else {
      await doSave()
    }

    dialogVisible.value = false
    // 智能刷新：仅刷新受影响的单条记录，避免全量重新加载
    if (savedRecordId) {
      const refreshed = await pageConfigStore.refreshSingleRecord(pageId.value, savedRecordId)
      if (refreshed) {
        tableData.value = [...pageConfigStore.getCachedPageData(pageId.value)]
      } else {
        // 刷新单条失败，回退到全量加载
        await loadPageData()
      }
    } else {
      await loadPageData()
    }
  } catch (error: any) {
    const resp = error.response?.data
    if (resp?.code === 'VERSION_CONFLICT') {
      ElMessage.error('数据已被其他用户修改，请刷新后重试')
      dialogVisible.value = false
      await loadPageData()
    } else if (resp?.validationErrors?.length) {
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
  const quoteDisplayMap = await pageConfigStore.fetchQuoteDisplayMaps(pageId.value)
  const mergedDisplayMap = { ...relationDisplayMap, ...quoteDisplayMap }
  exportToExcel(tableData.value, effectiveFields.value, name, mergedDisplayMap)
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
    const isJson = file.name.toLowerCase().endsWith('.json')
    const records = isJson
      ? await parseJsonImportFile(file, pageFields.value)
      : await parseImportFile(file, pageFields.value)
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

  // 创建共享缓存，避免多个 resolve 函数重复请求同一集合
  const collectionCache = new Map<string, any[]>()

  // 并行解析（使用共享缓存）
  await Promise.all([
    pageConfigStore.resolveRelationImportValues(pageId.value, records, collectionCache),
    pageConfigStore.resolveReferenceImportValues(pageId.value, records, collectionCache),
    pageConfigStore.resolveQuoteImportValues(pageId.value, records, collectionCache),
    pageConfigStore.resolveCollectionSelectImportValues(pageId.value, records, collectionCache)
  ])

  // 批量生成自增序列值
  const sequenceValues = pageConfigStore.batchGenerateSequenceValues(pageId.value, records.length)
  const sequenceFields = Object.keys(sequenceValues)

  let success = 0
  let failed = 0

  // 使用批量 API（≥ 10 条记录）
  if (records.length >= 10) {
    const BATCH_SIZE = 500
    const batches = Math.ceil(records.length / BATCH_SIZE)

    for (let batchIdx = 0; batchIdx < batches; batchIdx++) {
      const start = batchIdx * BATCH_SIZE
      const end = Math.min(start + BATCH_SIZE, records.length)
      const batchRecords = records.slice(start, end)

      try {
        // 准备批量请求数据
        const batchData = batchRecords.map((record, idx) => {
          const importId = record._importId as string | undefined
          const regularData = pageConfigStore.stripRelationFields(pageId.value, record)
          delete regularData._importId

          // 填充自增序列值
          for (const fieldName of sequenceFields) {
            if (!regularData[fieldName]) {
              regularData[fieldName] = sequenceValues[fieldName][start + idx]
            }
          }

          // 提取关联关系
          const relations: Record<string, string[]> = {}
          const relationFields = pageConfigStore.getRelationFields(pageId.value)
          for (const field of relationFields) {
            const ids = record[field.fieldName]
            if (Array.isArray(ids) && ids.length > 0) {
              relations[field.fieldName] = ids
            }
          }

          return {
            id: importId || `${collection.value}-${Math.random().toString(36).slice(2, 10)}`,
            data: regularData,
            relations
          }
        })

        // 调用批量创建 API
        const result = await post<{
          success: boolean
          created: number
          failed: number
          errors?: Array<{ index: number; error: string; record: any }>
          sequenceValues?: Record<string, string>
        }>(`/${collection.value}/batch-create`, {
          records: batchData,
          options: {
            skipValidation: false,
            generateSequence: true,
            continueOnError: true
          }
        })

        success += result.created
        failed += result.failed

        // 处理失败记录
        if (result.errors && result.errors.length > 0) {
          console.warn(`批次 ${batchIdx + 1} 失败记录:`, result.errors)
        }
      } catch (error) {
        console.error(`批次 ${batchIdx + 1} 导入失败:`, error)
        failed += batchRecords.length
      }

      importCurrent.value = end
      importProgress.value = Math.round((end / records.length) * 100)
    }
  } else {
    // 少量记录使用原有逐条导入
    await withBatch(`导入 ${records.length} 条${pageConfig.value?.name || '数据'}`, async () => {
      for (let i = 0; i < records.length; i++) {
        try {
          const importId = records[i]._importId as string | undefined
          const regularData = pageConfigStore.stripRelationFields(pageId.value, records[i])
          delete regularData._importId

          // 填充自增序列值
          for (const fieldName of sequenceFields) {
            if (!regularData[fieldName]) {
              regularData[fieldName] = sequenceValues[fieldName][i]
            }
          }

          const created = await pageConfigStore.addPageData(pageId.value, regularData, importId)
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
  }

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
 * 处理关联字段 Tag 点击 — 跳转到关联记录所在页面
 */
function handleRelationClick(relatedRecordId: string, field: FieldConfig): void {
  const targetCollection = field.relationConfig?.targetCollection
  if (!targetCollection) return

  const targetPageId = `page-${targetCollection}`
  const targetMenu = menuStore.menuList.find(m => m.pageId === targetPageId)
  if (!targetMenu?.path) {
    ElMessage.warning('未找到关联数据的页面')
    return
  }

  router.push({ path: targetMenu.path, query: { recordId: relatedRecordId } })
}

/**
 * 处理引用选择字段 Tag 点击 — 跳转到引用记录所在页面
 */
function handleQuoteClick(quotedRecordId: string, field: FieldConfig): void {
  const targetCollection = field.quoteConfig?.targetCollection
  if (!targetCollection) return

  const targetPageId = `page-${targetCollection}`
  const targetMenu = menuStore.menuList.find(m => m.pageId === targetPageId)
  if (!targetMenu?.path) {
    ElMessage.warning('未找到引用数据的页面')
    return
  }

  router.push({ path: targetMenu.path, query: { recordId: quotedRecordId } })
}

/**
 * 打开关系图谱对话框
 */
function handleShowRelationGraph(row: DynamicRecord): void {
  graphRecordId.value = row.id
  graphDialogVisible.value = true
}

/**
 * 处理图谱节点双击跳转
 */
function handleGraphNodeNavigate(targetCollection: string, targetRecordId: string): void {
  const targetPageId = `page-${targetCollection}`
  const targetMenu = menuStore.menuList.find(m => m.pageId === targetPageId)
  if (!targetMenu?.path) {
    ElMessage.warning('未找到目标数据的页面')
    return
  }
  router.push({ path: targetMenu.path, query: { recordId: targetRecordId } })
}

/**
 * 处理图谱节点编辑
 */
function handleGraphNodeEdit(targetCollection: string, targetRecordId: string): void {
  // 仅允许编辑当前页面的数据
  if (targetCollection !== collection.value) {
    ElMessage.info('跨页面编辑请先跳转到目标页面')
    return
  }
  const record = pageConfigStore.getCachedPageData(pageId.value)?.find(r => r.id === targetRecordId)
  if (!record) {
    ElMessage.warning('未找到对应记录')
    return
  }
  graphDialogVisible.value = false
  handleEdit(record)
}

/**
 * 高亮定位指定记录（从引用跳转过来时调用）
 *
 * 自动切换到目标记录所在的分页，然后滚动并高亮闪烁。
 */
async function highlightRecord(recordId: string): Promise<void> {
  await nextTick()

  // 在 filteredData 中查找记录的索引，确定所在分页
  const index = filteredData.value.findIndex(r => r.id === recordId)
  if (index === -1) return

  // 切换到目标记录所在的分页
  const targetPage = Math.floor(index / currentPageSize.value) + 1
  if (currentPage.value !== targetPage) {
    currentPage.value = targetPage
    // 同步通知 DataTable 组件更新内部页码
    const elDataTable = dataTableRef.value
    if (elDataTable) {
      handlePageChange(targetPage, currentPageSize.value)
    }
    await nextTick()
  }

  // 在分页后的数据中找到该行
  const row = paginatedData.value.find(r => r.id === recordId)
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
      currentPage.value = 1
      loadPageData()
    }
  },
  { immediate: true }
)

/**
 * 搜索关键字变化时重置到第一页
 */
watch(searchKeyword, () => {
  currentPage.value = 1
})

/**
 * 页面ID变化时清除筛选条件
 */
watch(pageId, () => {
  columnFilters.value = {}
  queryMode.value = false
  activeMongoQuery.value = null
  mongoQueryText.value = ''
})

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
    white-space: nowrap;
  }
}

.query-help {
  font-size: 13px;
  line-height: 1.6;

  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 8px;

    td {
      padding: 4px 8px;
      border-bottom: 1px solid #f0f0f0;
      vertical-align: top;
    }

    td:first-child {
      font-family: monospace;
      font-size: 12px;
      white-space: nowrap;
      color: #606266;
    }

    td:last-child {
      color: #909399;
    }
  }
}

.table-card {
  flex: 1;
  min-height: 0;
  overflow: hidden;

  :deep(.el-card__body) {
    height: 100%;
    padding: 16px;
    overflow: hidden;
    box-sizing: border-box;
  }

  :deep(.highlight-flash) {
    animation: row-flash 0.6s ease-in-out 3;
  }
}

@keyframes row-flash {
  0%, 100% { background-color: transparent; }
  50% { background-color: #ecf5ff; }
}

.relation-tags {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.relation-tag-link {
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    color: #409eff;
    border-color: #409eff;
  }
}

.reference-link {
  color: #409eff;
  cursor: pointer;

  &:hover {
    text-decoration: underline;
  }
}

.view-textarea {
  white-space: pre-wrap;
  word-break: break-all;
}

.view-richtext {
  max-height: 300px;
  overflow-y: auto;
  padding: 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}

.view-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.view-image-item {
  width: 80px;
  height: 80px;
  border-radius: 4px;
}
</style>
