/**
 * 数据表格组件
 *
 * 职责：
 * - 根据字段配置动态渲染表格列
 * - 支持分页、排序
 * - 集成操作按钮（查看、编辑、删除）
 *
 * 特性：
 * - 自动根据 controlType 格式化显示值
 * - 支持自定义操作列
 * - 响应式设计
 * - 虚拟滚动优化（大数据量）
 */
<template>
  <div ref="tableWrapperRef" class="data-table">
    <!-- 表格 -->
    <el-table
      ref="tableRef"
      :data="data"
      :loading="loading"
      :height="tableHeight"
      border
      stripe
      highlight-current-row
      @sort-change="handleSortChange"
      @selection-change="handleSelectionChange"
      style="width: 100%"
    >
      <!-- 多选列 -->
      <el-table-column v-if="showSelection" type="selection" width="45" align="center" />

      <!-- 序号列 -->
      <el-table-column type="index" label="序号" width="60" align="center" />

      <!-- 动态渲染数据列 -->
      <el-table-column
        v-for="field in visibleFields"
        :key="field.id"
        :prop="field.fieldName"
        :label="field.label"
        :sortable="isSortable(field)"
        :min-width="getColumnWidth(field)"
        :show-overflow-tooltip="!['relation', 'quoteSelect'].includes(field.controlType)"
      >
        <template #header>
          <div class="column-header" @click.stop="isFilterable(field) && openFilter(field)">
            <span>{{ field.label }}</span>
            <el-icon
              v-if="isFilterable(field)"
              class="filter-icon"
              :class="{ active: columnFilters[field.fieldName]?.value }"
            >
              <Search />
            </el-icon>
          </div>
        </template>
        <template #default="{ row }">
          <template v-if="field.controlType === 'relation'">
            <span v-if="!row[`_rel_${field.fieldName}_labels`]?.length">-</span>
            <span v-else class="relation-tags">
              <el-tag
                v-for="item in row[`_rel_${field.fieldName}_labels`].slice(0, 3)"
                :key="item.id"
                size="small"
                class="relation-tag-link"
                @click.stop="handleRelationClick(item.id, field)"
              >{{ item.label }}</el-tag>
              <el-tag
                v-if="row[`_rel_${field.fieldName}_labels`].length > 3"
                size="small"
                type="info"
              >+{{ row[`_rel_${field.fieldName}_labels`].length - 3 }}</el-tag>
            </span>
          </template>
          <template v-else-if="field.controlType === 'quoteSelect'">
            <span v-if="!row[`_quote_${field.fieldName}_labels`]?.length">-</span>
            <span v-else class="relation-tags">
              <el-tag
                v-for="item in row[`_quote_${field.fieldName}_labels`].slice(0, 3)"
                :key="item.id"
                size="small"
                class="relation-tag-link"
                @click.stop="handleQuoteClick(item.id, field)"
              >{{ item.label }}</el-tag>
              <el-tag
                v-if="row[`_quote_${field.fieldName}_labels`].length > 3"
                size="small"
                type="info"
              >+{{ row[`_quote_${field.fieldName}_labels`].length - 3 }}</el-tag>
            </span>
          </template>
          <span
            v-else-if="field.controlType === 'reference' && row[field.fieldName]"
            class="reference-link"
            @click.stop="handleReferenceClick(row, field)"
          >{{ getCellValue(row, field) }}</span>
          <span v-else>{{ getCellValue(row, field) }}</span>
        </template>
      </el-table-column>

      <!-- 操作列 -->
      <el-table-column
        label="操作"
        :width="showActions ? 250 : 80"
        align="center"
        fixed="right"
      >
        <template #default="{ row }">
          <el-button type="primary" link @click="handleView(row)">
            查看
          </el-button>
          <template v-if="showActions">
            <slot name="extra-actions" :row="row" />
            <el-button type="primary" link @click="handleEdit(row)">
              编辑
            </el-button>
            <el-button type="danger" link @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <!-- 分页 -->
    <div v-if="showPagination" class="pagination-container">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[20, 50, 100, 200, 500, 1000]"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="handleSizeChange"
        @current-change="handleCurrentChange"
      />
    </div>

    <!-- 单一筛选弹窗（移到表格外部，减少 DOM 节点） -->
    <Teleport to="body">
      <el-dialog
        v-model="filterDialogVisible"
        :title="filterDialogTitle"
        width="400px"
        :close-on-click-modal="true"
        append-to-body
      >
        <div class="filter-dialog-content">
          <el-input
            v-if="activeFilterField && ['relation', 'quoteSelect', 'reference'].includes(activeFilterField.controlType)"
            v-model="columnFilterForm.value"
            :placeholder="`输入${activeFilterField.label}关键字`"
            clearable
            style="width: 100%"
          />
          <el-select
            v-else-if="activeFilterField && ['select', 'radio', 'multiSelect', 'checkbox'].includes(activeFilterField.controlType)"
            v-model="columnFilterForm.value"
            :placeholder="`选择${activeFilterField.label}`"
            clearable
            :multiple="['multiSelect', 'checkbox'].includes(activeFilterField.controlType)"
            style="width: 100%"
          >
            <el-option
              v-for="opt in activeFilterField.options || []"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <template v-else-if="activeFilterField && ['date', 'datetime', 'autoTimestamp'].includes(activeFilterField.controlType)">
            <el-select v-model="columnFilterForm.operator" style="width: 100%; margin-bottom: 8px;">
              <el-option label="等于" value="eq" />
              <el-option label="早于" value="lt" />
              <el-option label="晚于" value="gt" />
              <el-option label="介于" value="between" />
            </el-select>
            <el-date-picker
              v-if="columnFilterForm.operator !== 'between'"
              v-model="columnFilterForm.value"
              :type="activeFilterField.controlType === 'date' ? 'date' : 'datetime'"
              :placeholder="`选择${activeFilterField.label}`"
              style="width: 100%"
            />
            <div v-else style="display: flex; gap: 8px;">
              <el-date-picker
                v-model="columnFilterForm.value"
                :type="activeFilterField.controlType === 'date' ? 'date' : 'datetime'"
                placeholder="开始"
                style="flex: 1"
              />
              <el-date-picker
                v-model="columnFilterForm.value2"
                :type="activeFilterField.controlType === 'date' ? 'date' : 'datetime'"
                placeholder="结束"
                style="flex: 1"
              />
            </div>
          </template>
          <template v-else-if="activeFilterField && activeFilterField.controlType === 'number'">
            <el-select v-model="columnFilterForm.operator" style="width: 100%; margin-bottom: 8px;">
              <el-option label="等于" value="eq" />
              <el-option label="大于" value="gt" />
              <el-option label="小于" value="lt" />
              <el-option label="介于" value="between" />
            </el-select>
            <el-input-number
              v-if="columnFilterForm.operator !== 'between'"
              v-model="columnFilterForm.value"
              :placeholder="`输入${activeFilterField.label}`"
              style="width: 100%"
            />
            <div v-else style="display: flex; gap: 8px; align-items: center;">
              <el-input-number v-model="columnFilterForm.value" placeholder="最小" style="flex: 1" />
              <span>-</span>
              <el-input-number v-model="columnFilterForm.value2" placeholder="最大" style="flex: 1" />
            </div>
          </template>
          <el-input
            v-else-if="activeFilterField"
            v-model="columnFilterForm.value"
            :placeholder="`输入${activeFilterField.label}关键字`"
            clearable
            style="width: 100%"
          />
        </div>
        <template #footer>
          <div style="display: flex; justify-content: space-between; width: 100%;">
            <el-button
              v-if="columnFilters[activeFilterField?.fieldName || '']?.value"
              type="danger"
              link
              @click="clearCurrentFilter"
            >清除筛选</el-button>
            <span v-else></span>
            <div>
              <el-button @click="filterDialogVisible = false">取消</el-button>
              <el-button type="primary" @click="applyFilter">确定</el-button>
            </div>
          </div>
        </template>
      </el-dialog>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
/**
 * DataTable 组件脚本
 *
 * Props：
 * - data: 表格数据
 * - fields: 字段配置列表
 * - loading: 加载状态
 * - showActions: 是否显示操作列
 * - showPagination: 是否显示分页
 * - total: 总记录数
 *
 * Events：
 * - view: 查看记录
 * - edit: 编辑记录
 * - delete: 删除记录
 * - page-change: 分页变化
 */
import { ref, computed, reactive, onMounted, onBeforeUnmount } from 'vue'
import { Search } from '@element-plus/icons-vue'
import type { FieldConfig, DynamicRecord } from '@/types'

interface ColumnFilter {
  value: any
  value2?: any
  operator?: string
}

// ==================== Props & Emits ====================

interface Props {
  /** 表格数据 */
  data: DynamicRecord[]
  /** 字段配置列表 */
  fields: FieldConfig[]
  /** 加载状态 */
  loading?: boolean
  /** 是否显示操作列 */
  showActions?: boolean
  /** 是否显示多选列 */
  showSelection?: boolean
  /** 是否显示分页 */
  showPagination?: boolean
  /** 总记录数 */
  total?: number
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  showActions: true,
  showSelection: false,
  showPagination: true,
  total: 0
})

const emit = defineEmits<{
  (e: 'view', row: DynamicRecord): void
  (e: 'edit', row: DynamicRecord): void
  (e: 'delete', row: DynamicRecord): void
  (e: 'reference-click', row: DynamicRecord, field: FieldConfig): void
  (e: 'relation-click', relatedRecordId: string, field: FieldConfig): void
  (e: 'quote-click', quotedRecordId: string, field: FieldConfig): void
  (e: 'page-change', page: number, pageSize: number): void
  (e: 'sort-change', field: string, order: string): void
  (e: 'selection-change', rows: DynamicRecord[]): void
  (e: 'filter-change', filters: Record<string, ColumnFilter>): void
}>()

// ==================== State ====================

const tableRef = ref()
const tableWrapperRef = ref<HTMLElement>()
const wrapperHeight = ref(0)

const tableHeight = computed(() => {
  if (!wrapperHeight.value) return undefined
  const paginationH = props.showPagination ? 52 : 0
  return Math.max(200, wrapperHeight.value - paginationH)
})

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  if (tableWrapperRef.value) {
    resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        wrapperHeight.value = entry.contentRect.height
      }
    })
    resizeObserver.observe(tableWrapperRef.value)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
})

const currentPage = ref(1)
const pageSize = ref(50)

const columnFilters = ref<Record<string, ColumnFilter>>({})

// 筛选弹窗状态
const filterDialogVisible = ref(false)
const activeFilterField = ref<FieldConfig | null>(null)
const columnFilterForm = reactive<ColumnFilter>({
  value: null,
  value2: null,
  operator: 'eq'
})

// ==================== 计算属性 ====================

const visibleFields = computed(() => {
  return props.fields
    .filter((field) => !field.hidden)
    .sort((a, b) => a.order - b.order)
})

/** 筛选弹窗标题 */
const filterDialogTitle = computed(() => {
  return activeFilterField.value ? `筛选 "${activeFilterField.value.label}"` : '筛选'
})

// ==================== 单元格值缓存 ====================

/** 缓存格式化后的单元格值，避免重复计算 */
const cellValueCache = new Map<string, string>()

/** 清除缓存（数据变化时调用） */
function clearCellValueCache(): void {
  cellValueCache.clear()
}

/** 获取单元格值（带缓存） */
function getCellValue(row: DynamicRecord, field: FieldConfig): string {
  // 使用 recordId + fieldName 作为缓存 key
  const cacheKey = `${row.id}_${field.fieldName}`
  const cached = cellValueCache.get(cacheKey)
  if (cached !== undefined) {
    return cached
  }

  const value = formatCellValue(row, field)
  cellValueCache.set(cacheKey, value)
  return value
}

// ==================== 方法 ====================

function isFilterable(field: FieldConfig): boolean {
  const filterableTypes = ['text', 'textarea', 'number', 'date', 'datetime', 'autoTimestamp', 'autoSequence', 'select', 'radio', 'multiSelect', 'checkbox', 'relation', 'quoteSelect', 'reference', 'compositeText']
  return filterableTypes.includes(field.controlType)
}

/** 打开筛选弹窗 */
function openFilter(field: FieldConfig): void {
  activeFilterField.value = field
  const existing = columnFilters.value[field.fieldName]
  columnFilterForm.value = existing?.value ?? null
  columnFilterForm.value2 = existing?.value2 ?? null
  columnFilterForm.operator = existing?.operator ?? 'eq'
  filterDialogVisible.value = true
}

/** 应用筛选 */
function applyFilter(): void {
  if (!activeFilterField.value) return

  const fieldName = activeFilterField.value.fieldName
  if (columnFilterForm.value !== null && columnFilterForm.value !== '' && columnFilterForm.value !== undefined) {
    columnFilters.value[fieldName] = {
      value: columnFilterForm.value,
      value2: columnFilterForm.value2,
      operator: columnFilterForm.operator
    }
  } else {
    delete columnFilters.value[fieldName]
  }
  filterDialogVisible.value = false
  emit('filter-change', columnFilters.value)
}

/** 清除当前筛选 */
function clearCurrentFilter(): void {
  if (!activeFilterField.value) return
  delete columnFilters.value[activeFilterField.value.fieldName]
  columnFilterForm.value = null
  columnFilterForm.value2 = null
  columnFilterForm.operator = 'eq'
  filterDialogVisible.value = false
  emit('filter-change', columnFilters.value)
}

function isSortable(field: FieldConfig): boolean | 'custom' {
  const sortableTypes = ['text', 'number', 'date', 'datetime', 'autoTimestamp', 'autoSequence', 'compositeText']
  return sortableTypes.includes(field.controlType) ? 'custom' : false
}

function getColumnWidth(field: FieldConfig): string {
  if (field.width) return field.width
  switch (field.controlType) {
    case 'textarea':
      return '200'
    case 'date':
    case 'datetime':
    case 'autoTimestamp':
      return '180'
    case 'autoSequence':
      return '150'
    case 'select':
    case 'multiSelect':
    case 'reference':
      return '120'
    case 'relation':
      return '200'
    case 'quoteSelect':
      return '200'
    case 'compositeText':
      return '200'
    case 'richText':
      return '200'
    default:
      return '150'
  }
}

/**
 * 格式化单元格值
 *
 * 根据字段控件类型进行格式化显示
 */
function formatCellValue(row: any, field: FieldConfig): string {
  const value = row[field.fieldName]
  if (value === null || value === undefined || value === '') {
    return '-'
  }

  switch (field.controlType) {
    case 'select':
    case 'radio': {
      const option = field.options?.find((opt) => opt.value === value)
      return option?.label || String(value)
    }

    case 'multiSelect':
    case 'checkbox':
      if (Array.isArray(value)) {
        return value
          .map((v) => {
            const opt = field.options?.find((o) => o.value === v)
            return opt?.label || String(v)
          })
          .join('、')
      }
      return String(value)

    case 'date':
      return formatDate(value, 'YYYY-MM-DD')

    case 'datetime':
    case 'autoTimestamp':
      return formatDate(value, 'YYYY-MM-DD HH:mm:ss')

    case 'autoSequence':
      return String(value)

    case 'file':
    case 'image':
      if (Array.isArray(value)) {
        return `${value.length} 个文件`
      }
      return '-'

    case 'relation':
      if (Array.isArray(value)) {
        return value.length === 0 ? '-' : `${value.length} 条关联`
      }
      return String(value)

    case 'quoteSelect':
      if (Array.isArray(value)) {
        return value.length === 0 ? '-' : `${value.length} 条引用`
      }
      return String(value)

    case 'reference': {
      const displayVal = row[`_ref_${field.fieldName}_display`]
      return displayVal || String(value)
    }

    case 'richText': {
      const plain = value?.replace(/<[^>]*>/g, '') || ''
      return plain.length > 50 ? plain.slice(0, 50) + '...' : plain || '-'
    }

    default:
      return String(value)
  }
}

/**
 * 简单日期格式化
 */
function formatDate(value: string, format: string): string {
  if (!value) return '-'
  try {
    const date = new Date(value)
    if (isNaN(date.getTime())) return value

    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    const hours = String(date.getHours()).padStart(2, '0')
    const minutes = String(date.getMinutes()).padStart(2, '0')
    const seconds = String(date.getSeconds()).padStart(2, '0')

    if (format === 'YYYY-MM-DD') {
      return `${year}-${month}-${day}`
    }
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
  } catch {
    return value
  }
}

function handleView(row: DynamicRecord): void {
  emit('view', row)
}

function handleEdit(row: DynamicRecord): void {
  emit('edit', row)
}

function handleDelete(row: DynamicRecord): void {
  emit('delete', row)
}

function handleSizeChange(size: number): void {
  pageSize.value = size
  currentPage.value = 1
  emit('page-change', 1, size)
}

function handleCurrentChange(page: number): void {
  currentPage.value = page
  emit('page-change', page, pageSize.value)
}

function handleSortChange({ prop, order }: { prop: string; order: string }): void {
  emit('sort-change', prop, order === 'ascending' ? 'asc' : 'desc')
}

function handleReferenceClick(row: DynamicRecord, field: FieldConfig): void {
  emit('reference-click', row, field)
}

function handleRelationClick(relatedRecordId: string, field: FieldConfig): void {
  emit('relation-click', relatedRecordId, field)
}

function handleQuoteClick(quotedRecordId: string, field: FieldConfig): void {
  emit('quote-click', quotedRecordId, field)
}

function handleSelectionChange(rows: DynamicRecord[]): void {
  emit('selection-change', rows)
}

function clearSelection(): void {
  tableRef.value?.clearSelection()
}

function clearAllFilters(): void {
  columnFilters.value = {}
  clearCellValueCache()
  emit('filter-change', columnFilters.value)
}

// ==================== 暴露 ====================

defineExpose({ tableRef, clearSelection, clearAllFilters, clearCellValueCache })
</script>

<style scoped lang="scss">
.data-table {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.column-header {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;

  .filter-icon {
    font-size: 14px;
    color: #c0c4cc;
    transition: color 0.2s;

    &:hover {
      color: #409eff;
    }

    &.active {
      color: #409eff;
    }
  }
}

.filter-dialog-content {
  padding: 0 0 16px 0;
}

.reference-link {
  color: #409eff;
  cursor: pointer;

  &:hover {
    text-decoration: underline;
  }
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

.pagination-container {
  display: flex;
  justify-content: flex-end;
  padding: 16px 0;
}
</style>