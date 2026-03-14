/**
 * Excel 视图组件
 *
 * 职责：
 * - 以电子表格形式展示数据
 * - 支持 Excel 风格的基础数据过滤能力
 * - 仅限查看不可修改
 *
 * 性能优化：
 * - 分页渲染，避免大数据量卡顿
 * - 搜索防抖
 * - 简化单元格渲染
 */
<template>
  <div class="excel-view">
    <!-- 工具栏 -->
    <div class="excel-toolbar">
      <div class="toolbar-left">
        <el-input
          v-model="searchText"
          placeholder="搜索..."
          clearable
          :prefix-icon="Search"
          style="width: 240px"
          @input="handleSearchInput"
        />
        <el-button :disabled="!hasActiveFilters" @click="clearAllFilters">
          <el-icon><RefreshRight /></el-icon>
          清除筛选
        </el-button>
      </div>
      <div class="toolbar-right">
        <el-button type="primary" @click="handleExport">
          <el-icon><Download /></el-icon>
          导出
        </el-button>
      </div>
    </div>

    <!-- 表格 -->
    <div ref="tableWrapperRef" class="excel-table-wrapper">
      <el-table
        ref="tableRef"
        :data="paginatedData"
        :loading="loading"
        :height="tableHeight"
        border
        stripe
        highlight-current-row
        @sort-change="handleSortChange"
        @row-click="handleRowClick"
      >
        <!-- 序号列 -->
        <el-table-column type="index" label="#" width="60" fixed align="center" :index="indexMethod" />

        <!-- 动态数据列 -->
        <el-table-column
          v-for="field in visibleFields"
          :key="field.id"
          :prop="field.fieldName"
          :label="field.label"
          :sortable="isSortable(field) ? 'custom' : false"
          :min-width="getColumnWidth(field)"
          :show-overflow-tooltip="!['relation', 'quoteSelect'].includes(field.controlType)"
        >
          <template #header>
            <div class="column-header">
              <span>{{ field.label }}</span>
              <el-icon
                v-if="isFilterable(field)"
                class="filter-icon"
                :class="{ active: columnFilters[field.fieldName]?.value }"
                @click.stop="openFilterDialog(field)"
              >
                <Search />
              </el-icon>
            </div>
          </template>
          <template #default="{ row }">
            <CellContent
              :row="row"
              :field="field"
              @relation-click="handleRelationClick"
              @quote-click="handleQuoteClick"
              @reference-click="handleReferenceClick"
            />
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 分页和状态栏 -->
    <div class="excel-footer">
      <div class="status-info">
        共 {{ data.length }} 条记录
        <template v-if="filteredCount !== data.length">，筛选后 {{ filteredCount }} 条</template>
      </div>
      <el-pagination
        v-if="filteredCount > pageSize"
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[50, 100, 200, 500]"
        :total="filteredCount"
        layout="sizes, prev, pager, next, jumper"
        small
        @size-change="handlePageSizeChange"
      />
    </div>

    <!-- 筛选对话框（延迟渲染，避免每列都创建） -->
    <el-dialog
      v-model="filterDialogVisible"
      :title="filterDialogTitle"
      width="320px"
      :close-on-click-modal="false"
    >
      <div class="filter-dialog-content">
        <!-- 关联/引用类型字段 -->
        <el-input
          v-if="currentFilterField && ['relation', 'quoteSelect', 'reference'].includes(currentFilterField.controlType)"
          v-model="filterForm.value"
          :placeholder="`输入关键字`"
          clearable
        />
        <!-- 选项类字段 -->
        <el-select
          v-else-if="currentFilterField && ['select', 'radio', 'multiSelect', 'checkbox'].includes(currentFilterField.controlType)"
          v-model="filterForm.value"
          :placeholder="`选择`"
          clearable
          :multiple="['multiSelect', 'checkbox'].includes(currentFilterField.controlType)"
          style="width: 100%"
        >
          <el-option
            v-for="opt in currentFilterField.options || []"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <!-- 日期时间类型字段 -->
        <template v-else-if="currentFilterField && ['date', 'datetime', 'autoTimestamp'].includes(currentFilterField.controlType)">
          <el-select v-model="filterForm.operator" style="width: 100%; margin-bottom: 8px;">
            <el-option label="等于" value="eq" />
            <el-option label="早于" value="lt" />
            <el-option label="晚于" value="gt" />
            <el-option label="介于" value="between" />
          </el-select>
          <el-date-picker
            v-if="filterForm.operator !== 'between'"
            v-model="filterForm.value"
            :type="currentFilterField.controlType === 'date' ? 'date' : 'datetime'"
            placeholder="选择日期"
            style="width: 100%"
          />
          <div v-else style="display: flex; gap: 8px;">
            <el-date-picker
              v-model="filterForm.value"
              :type="currentFilterField.controlType === 'date' ? 'date' : 'datetime'"
              placeholder="开始"
              style="flex: 1"
            />
            <el-date-picker
              v-model="filterForm.value2"
              :type="currentFilterField.controlType === 'date' ? 'date' : 'datetime'"
              placeholder="结束"
              style="flex: 1"
            />
          </div>
        </template>
        <!-- 数字类型字段 -->
        <template v-else-if="currentFilterField && currentFilterField.controlType === 'number'">
          <el-select v-model="filterForm.operator" style="width: 100%; margin-bottom: 8px;">
            <el-option label="等于" value="eq" />
            <el-option label="大于" value="gt" />
            <el-option label="小于" value="lt" />
            <el-option label="介于" value="between" />
          </el-select>
          <el-input-number
            v-if="filterForm.operator !== 'between'"
            v-model="filterForm.value"
            placeholder="输入值"
            style="width: 100%"
          />
          <div v-else style="display: flex; gap: 8px; align-items: center;">
            <el-input-number v-model="filterForm.value" placeholder="最小" style="flex: 1" />
            <span>-</span>
            <el-input-number v-model="filterForm.value2" placeholder="最大" style="flex: 1" />
          </div>
        </template>
        <!-- 其他类型字段 -->
        <el-input
          v-else
          v-model="filterForm.value"
          placeholder="输入关键字"
          clearable
        />
      </div>
      <template #footer>
        <el-button size="small" @click="filterDialogVisible = false">取消</el-button>
        <el-button
          v-if="currentFilterFieldName && columnFilters[currentFilterFieldName]?.value"
          type="danger"
          size="small"
          @click="clearCurrentFilter"
        >清除</el-button>
        <el-button type="primary" size="small" @click="applyCurrentFilter">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, shallowRef, onMounted, onBeforeUnmount, watch, defineComponent, h } from 'vue'
import { Search, Download, RefreshRight } from '@element-plus/icons-vue'
import type { FieldConfig, DynamicRecord } from '@/types'
import { exportToExcel } from '@/utils/excel'

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
}

const props = withDefaults(defineProps<Props>(), {
  loading: false
})

const emit = defineEmits<{
  (e: 'row-click', row: DynamicRecord): void
  (e: 'reference-click', row: DynamicRecord, field: FieldConfig): void
  (e: 'relation-click', relatedRecordId: string, field: FieldConfig): void
  (e: 'quote-click', quotedRecordId: string, field: FieldConfig): void
  (e: 'export', data: DynamicRecord[]): void
}>()

// ==================== 轻量级单元格组件 ====================

const CellContent = defineComponent({
  name: 'CellContent',
  props: {
    row: { type: Object, required: true },
    field: { type: Object as () => FieldConfig, required: true }
  },
  emits: ['relation-click', 'quote-click', 'reference-click'],
  setup(props, { emit }) {
    return () => {
      const row = props.row as DynamicRecord
      const field = props.field
      const value = row[field.fieldName]

      // 关联字段
      if (field.controlType === 'relation') {
        const labels = row[`_rel_${field.fieldName}_labels`]
        if (!Array.isArray(labels) || labels.length === 0) return h('span', '-')
        const items = labels.slice(0, 3).map((item: any) =>
          h('span', {
            class: 'cell-tag',
            onClick: (e: Event) => { e.stopPropagation(); emit('relation-click', item.id, field) }
          }, item.label)
        )
        if (labels.length > 3) {
          items.push(h('span', { class: 'cell-tag cell-tag-more' }, `+${labels.length - 3}`))
        }
        return h('span', { class: 'cell-tags' }, items)
      }

      // 引用选择字段
      if (field.controlType === 'quoteSelect') {
        const labels = row[`_quote_${field.fieldName}_labels`]
        if (!Array.isArray(labels) || labels.length === 0) return h('span', '-')
        const items = labels.slice(0, 3).map((item: any) =>
          h('span', {
            class: 'cell-tag',
            onClick: (e: Event) => { e.stopPropagation(); emit('quote-click', item.id, field) }
          }, item.label)
        )
        if (labels.length > 3) {
          items.push(h('span', { class: 'cell-tag cell-tag-more' }, `+${labels.length - 3}`))
        }
        return h('span', { class: 'cell-tags' }, items)
      }

      // 引用字段
      if (field.controlType === 'reference' && value) {
        const display = row[`_ref_${field.fieldName}_display`]
        return h('span', {
          class: 'cell-link',
          onClick: (e: Event) => { e.stopPropagation(); emit('reference-click', row, field) }
        }, display || String(value))
      }

      // 其他类型
      return h('span', formatValue(value, field, row))
    }
  }
})

// ==================== State ====================

const tableRef = ref()
const tableWrapperRef = ref<HTMLElement>()
const wrapperHeight = ref(0)

const searchText = ref('')
const debouncedSearchText = ref('')
// 使用 shallowRef 避免深层响应式，提升大数据性能
const columnFilters = shallowRef<Record<string, ColumnFilter>>({})

// 分页
const currentPage = ref(1)
const pageSize = ref(100)

// 筛选对话框
const filterDialogVisible = ref(false)
const currentFilterFieldName = ref<string>('')
const currentFilterField = ref<FieldConfig | null>(null)
const filterForm = reactive<ColumnFilter>({
  value: null,
  value2: null,
  operator: 'eq'
})

// 排序
const sortField = ref<string>('')
const sortDirection = ref<'asc' | 'desc'>('asc')

// 防抖定时器
let searchTimer: ReturnType<typeof setTimeout> | null = null

// ResizeObserver
let resizeObserver: ResizeObserver | null = null

// ==================== 性能优化：缓存 ====================

// 缓存过滤结果，避免重复计算
const filterResultCache = {
  keyword: '',
  filters: {} as Record<string, ColumnFilter>,
  filteredIds: new Set<string>(),
  version: 0
}

// 缓存排序结果
const sortResultCache = {
  field: '',
  direction: '' as 'asc' | 'desc' | '',
  sortedData: null as DynamicRecord[] | null
}

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
  if (searchTimer) clearTimeout(searchTimer)
})

// ==================== 计算属性 ====================

const tableHeight = computed(() => {
  if (!wrapperHeight.value) return undefined
  return Math.max(200, wrapperHeight.value - 60)
})

const visibleFields = computed(() => {
  return props.fields
    .filter((field) => !field.hidden)
    .sort((a, b) => a.order - b.order)
})

const hasActiveFilters = computed(() => {
  return Object.keys(columnFilters.value).length > 0 || searchText.value.trim() !== ''
})

const filterDialogTitle = computed(() => {
  return `筛选 "${currentFilterField.value?.label || ''}"`
})

// ==================== 性能优化：增量过滤 ====================

/**
 * 执行过滤并返回匹配的记录ID集合
 * 使用缓存策略，仅在条件变化时重新计算
 */
function getFilteredIds(): Set<string> {
  const data = props.data
  const keyword = debouncedSearchText.value.trim().toLowerCase()
  const filters = columnFilters.value
  const filterKey = JSON.stringify(filters)

  // 检查缓存是否有效
  if (filterResultCache.keyword === keyword &&
      JSON.stringify(filterResultCache.filters) === filterKey) {
    return filterResultCache.filteredIds
  }

  // 重新计算
  const filteredIds = new Set<string>()
  const hasKeyword = keyword !== ''
  const filterEntries = Object.entries(filters)
  const hasFilters = filterEntries.length > 0
  const fields = visibleFields.value

  // 快速路径：无筛选条件
  if (!hasKeyword && !hasFilters) {
    for (let i = 0; i < data.length; i++) {
      filteredIds.add(data[i].id)
    }
  } else {
    for (let i = 0; i < data.length; i++) {
      if (matchFiltersFast(data[i], keyword, hasKeyword, filterEntries, hasFilters, fields)) {
        filteredIds.add(data[i].id)
      }
    }
  }

  // 更新缓存
  filterResultCache.keyword = keyword
  filterResultCache.filters = { ...filters }
  filterResultCache.filteredIds = filteredIds
  filterResultCache.version++

  return filteredIds
}

/**
 * 快速过滤匹配函数（优化版）
 * 减少函数调用和对象创建
 */
function matchFiltersFast(
  record: DynamicRecord,
  keyword: string,
  hasKeyword: boolean,
  filterEntries: [string, ColumnFilter][],
  hasFilters: boolean,
  fields: FieldConfig[]
): boolean {
  // 全文搜索
  if (hasKeyword) {
    let matched = false
    for (let i = 0; i < fields.length; i++) {
      const field = fields[i]
      const val = record[field.fieldName]
      if (val == null) continue

      const controlType = field.controlType

      if (controlType === 'text' || controlType === 'textarea' ||
          controlType === 'number' || controlType === 'autoSequence') {
        if (String(val).toLowerCase().includes(keyword)) { matched = true; break }
      } else if (controlType === 'select' || controlType === 'radio') {
        const opts = field.options
        if (opts) {
          for (let j = 0; j < opts.length; j++) {
            if (opts[j].value === val && opts[j].label.toLowerCase().includes(keyword)) {
              matched = true; break
            }
          }
          if (matched) break
        }
      } else if (controlType === 'multiSelect' || controlType === 'checkbox') {
        const opts = field.options
        if (opts && Array.isArray(val)) {
          for (let j = 0; j < val.length; j++) {
            const opt = opts.find(o => o.value === val[j])
            if (opt && opt.label.toLowerCase().includes(keyword)) {
              matched = true; break
            }
          }
          if (matched) break
        }
      } else if (controlType === 'relation') {
        const labels = record[`_rel_${field.fieldName}_labels`]
        if (Array.isArray(labels)) {
          for (let j = 0; j < labels.length; j++) {
            if (labels[j].label.toLowerCase().includes(keyword)) { matched = true; break }
          }
          if (matched) break
        }
      } else if (controlType === 'reference') {
        const display = record[`_ref_${field.fieldName}_display`]
        if (display && String(display).toLowerCase().includes(keyword)) { matched = true; break }
      } else if (controlType === 'quoteSelect') {
        const labels = record[`_quote_${field.fieldName}_labels`]
        if (Array.isArray(labels)) {
          for (let j = 0; j < labels.length; j++) {
            if (labels[j].label.toLowerCase().includes(keyword)) { matched = true; break }
          }
          if (matched) break
        }
      } else if (controlType === 'date' || controlType === 'datetime' || controlType === 'autoTimestamp') {
        if (String(val).toLowerCase().includes(keyword)) { matched = true; break }
      }
    }
    if (!matched) return false
  }

  // 列筛选
  if (hasFilters) {
    for (let i = 0; i < filterEntries.length; i++) {
      const [fieldName, filter] = filterEntries[i]
      const field = fields.find(f => f.fieldName === fieldName)
      if (!field) continue

      const filterVal = filter.value
      if (filterVal == null || filterVal === '') continue

      const controlType = field.controlType

      if (controlType === 'relation') {
        const labels = record[`_rel_${fieldName}_labels`]
        if (!Array.isArray(labels) || labels.length === 0) return false
        const kw = String(filterVal).toLowerCase()
        let found = false
        for (let j = 0; j < labels.length; j++) {
          if (labels[j].label.toLowerCase().includes(kw)) { found = true; break }
        }
        if (!found) return false
      } else if (controlType === 'quoteSelect') {
        const labels = record[`_quote_${fieldName}_labels`]
        if (!Array.isArray(labels) || labels.length === 0) return false
        const kw = String(filterVal).toLowerCase()
        let found = false
        for (let j = 0; j < labels.length; j++) {
          if (labels[j].label.toLowerCase().includes(kw)) { found = true; break }
        }
        if (!found) return false
      } else if (controlType === 'reference') {
        const display = record[`_ref_${fieldName}_display`]
        if (!display || !String(display).toLowerCase().includes(String(filterVal).toLowerCase())) return false
      } else if (controlType === 'select' || controlType === 'radio') {
        if (record[fieldName] !== filterVal) return false
      } else if (controlType === 'multiSelect' || controlType === 'checkbox') {
        const val = record[fieldName]
        if (!Array.isArray(val) || !Array.isArray(filterVal)) return false
        for (let j = 0; j < filterVal.length; j++) {
          if (!val.includes(filterVal[j])) return false
        }
      } else if (controlType === 'number') {
        const n = Number(record[fieldName])
        const n1 = Number(filterVal)
        const op = filter.operator
        if (op === 'eq' && n !== n1) return false
        else if (op === 'gt' && n <= n1) return false
        else if (op === 'lt' && n >= n1) return false
        else if (op === 'between') {
          const n2 = Number(filter.value2)
          if (n < n1 || n > n2) return false
        }
      } else if (controlType === 'date' || controlType === 'datetime' || controlType === 'autoTimestamp') {
        const recordDate = new Date(record[fieldName]).getTime()
        const f1 = filterVal ? new Date(filterVal).getTime() : 0
        const f2 = filter.value2 ? new Date(filter.value2).getTime() : 0
        const op = filter.operator
        if (op === 'eq' && f1 && recordDate !== f1) return false
        else if (op === 'lt' && f1 && recordDate >= f1) return false
        else if (op === 'gt' && f1 && recordDate <= f1) return false
        else if (op === 'between' && f1 && f2 && (recordDate < f1 || recordDate > f2)) return false
      } else {
        // 文本类型
        const val = record[fieldName]
        if (val == null || val === '') return false
        if (!String(val).toLowerCase().includes(String(filterVal).toLowerCase())) return false
      }
    }
  }

  return true
}

const filteredCount = computed(() => {
  // 无筛选条件时直接返回数据长度
  const keyword = debouncedSearchText.value.trim()
  const filters = columnFilters.value
  if (!keyword && Object.keys(filters).length === 0) {
    return props.data.length
  }
  return getFilteredIds().size
})

// 分页数据 - 使用缓存优化
const paginatedData = computed(() => {
  const data = props.data
  const keyword = debouncedSearchText.value.trim()
  const filters = columnFilters.value
  const hasKeyword = keyword !== ''
  const hasFilters = Object.keys(filters).length > 0

  // 快速路径：无筛选，直接分页
  if (!hasKeyword && !hasFilters) {
    const start = (currentPage.value - 1) * pageSize.value
    const end = start + pageSize.value

    // 排序优化：检查缓存
    if (sortField.value) {
      const needSort = sortResultCache.field !== sortField.value ||
                       sortResultCache.direction !== sortDirection.value

      if (needSort || sortResultCache.sortedData === null) {
        sortResultCache.field = sortField.value
        sortResultCache.direction = sortDirection.value
        sortResultCache.sortedData = sortData(data.slice(), sortField.value, sortDirection.value, visibleFields.value)
      }
      return sortResultCache.sortedData.slice(start, end)
    }

    return data.slice(start, end)
  }

  // 有筛选：使用缓存的过滤结果
  const filteredIds = getFilteredIds()
  const filtered: DynamicRecord[] = []

  for (let i = 0; i < data.length; i++) {
    if (filteredIds.has(data[i].id)) {
      filtered.push(data[i])
    }
  }

  // 排序
  if (sortField.value) {
    return sortData(filtered, sortField.value, sortDirection.value, visibleFields.value)
      .slice((currentPage.value - 1) * pageSize.value, currentPage.value * pageSize.value)
  }

  return filtered.slice((currentPage.value - 1) * pageSize.value, currentPage.value * pageSize.value)
})

// ==================== 方法 ====================

function sortData(data: DynamicRecord[], field: string, direction: 'asc' | 'desc', fields: FieldConfig[]): DynamicRecord[] {
  const fieldConfig = fields.find(f => f.fieldName === field)
  if (!fieldConfig) return data

  const isNum = fieldConfig.controlType === 'number'
  const isDate = ['date', 'datetime', 'autoTimestamp'].includes(fieldConfig.controlType)
  const mult = direction === 'asc' ? 1 : -1

  return data.slice().sort((a, b) => {
    const aVal = a[field] ?? ''
    const bVal = b[field] ?? ''

    if (isNum) {
      return (Number(aVal) - Number(bVal)) * mult
    }
    if (isDate) {
      return ((new Date(aVal).getTime() || 0) - (new Date(bVal).getTime() || 0)) * mult
    }
    return String(aVal).localeCompare(String(bVal), 'zh-CN') * mult
  })
}

function isFilterable(field: FieldConfig): boolean {
  return ['text', 'textarea', 'number', 'date', 'datetime', 'autoTimestamp', 'autoSequence', 'select', 'radio', 'multiSelect', 'checkbox', 'relation', 'quoteSelect', 'reference'].includes(field.controlType)
}

function isSortable(field: FieldConfig): boolean {
  return ['text', 'number', 'date', 'datetime', 'autoTimestamp', 'autoSequence'].includes(field.controlType)
}

function getColumnWidth(field: FieldConfig): string {
  const widths: Record<string, string> = {
    textarea: '200', date: '150', datetime: '170', autoTimestamp: '170',
    autoSequence: '120', select: '120', multiSelect: '140', reference: '120',
    relation: '180', quoteSelect: '180', richText: '200'
  }
  return widths[field.controlType] || '150'
}

function formatValue(value: any, field: FieldConfig, row: any): string {
  if (value == null || value === '') return '-'

  switch (field.controlType) {
    case 'select':
    case 'radio':
      return field.options?.find(o => o.value === value)?.label || String(value)
    case 'multiSelect':
    case 'checkbox':
      return Array.isArray(value)
        ? value.map(v => field.options?.find(o => o.value === v)?.label || String(v)).join('、')
        : String(value)
    case 'date':
      return formatDateStr(value, false)
    case 'datetime':
    case 'autoTimestamp':
      return formatDateStr(value, true)
    case 'reference':
      return row[`_ref_${field.fieldName}_display`] || String(value)
    case 'richText':
      const plain = String(value).replace(/<[^>]*>/g, '')
      return plain.length > 50 ? plain.slice(0, 50) + '...' : plain || '-'
    case 'file':
    case 'image':
      return Array.isArray(value) ? `${value.length} 个文件` : '-'
    default:
      return String(value)
  }
}

function formatDateStr(value: string, withTime: boolean): string {
  if (!value) return '-'
  try {
    const d = new Date(value)
    if (isNaN(d.getTime())) return value
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    if (!withTime) return `${y}-${m}-${day}`
    const h = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    const s = String(d.getSeconds()).padStart(2, '0')
    return `${y}-${m}-${day} ${h}:${min}:${s}`
  } catch {
    return value
  }
}

// 搜索防抖
function handleSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    debouncedSearchText.value = searchText.value
    currentPage.value = 1
    // 清除过滤缓存
    filterResultCache.keyword = ''
    filterResultCache.version = 0
  }, 300)
}

// 分页序号
function indexMethod(index: number): number {
  return (currentPage.value - 1) * pageSize.value + index + 1
}

function handlePageSizeChange() {
  currentPage.value = 1
}

function openFilterDialog(field: FieldConfig) {
  currentFilterFieldName.value = field.fieldName
  currentFilterField.value = field
  const existing = columnFilters.value[field.fieldName]
  filterForm.value = existing?.value ?? null
  filterForm.value2 = existing?.value2 ?? null
  filterForm.operator = existing?.operator ?? 'eq'
  filterDialogVisible.value = true
}

function applyCurrentFilter() {
  const fieldName = currentFilterFieldName.value
  if (!fieldName) return

  const newFilters = { ...columnFilters.value }
  if (filterForm.value != null && filterForm.value !== '' && filterForm.value !== undefined) {
    newFilters[fieldName] = {
      value: filterForm.value,
      value2: filterForm.value2,
      operator: filterForm.operator
    }
  } else {
    delete newFilters[fieldName]
  }
  columnFilters.value = newFilters
  currentPage.value = 1
  // 清除缓存
  filterResultCache.filters = {}
  filterResultCache.version = 0
  filterDialogVisible.value = false
}

function clearCurrentFilter() {
  const fieldName = currentFilterFieldName.value
  if (!fieldName) return

  const newFilters = { ...columnFilters.value }
  delete newFilters[fieldName]
  columnFilters.value = newFilters
  currentPage.value = 1
  // 清除缓存
  filterResultCache.filters = {}
  filterResultCache.version = 0
  filterDialogVisible.value = false
}

function clearAllFilters() {
  columnFilters.value = {}
  searchText.value = ''
  debouncedSearchText.value = ''
  currentPage.value = 1
  // 清除所有缓存
  filterResultCache.keyword = ''
  filterResultCache.filters = {}
  filterResultCache.filteredIds.clear()
  filterResultCache.version = 0
  sortResultCache.field = ''
  sortResultCache.sortedData = null
}

function handleSortChange({ prop, order }: { prop: string; order: string }) {
  if (order) {
    sortField.value = prop
    sortDirection.value = order === 'ascending' ? 'asc' : 'desc'
  } else {
    sortField.value = ''
  }
  currentPage.value = 1
}

function handleRowClick(row: DynamicRecord) {
  emit('row-click', row)
}

function handleReferenceClick(row: DynamicRecord, field: FieldConfig) {
  emit('reference-click', row, field)
}

function handleRelationClick(id: string, field: FieldConfig) {
  emit('relation-click', id, field)
}

function handleQuoteClick(id: string, field: FieldConfig) {
  emit('quote-click', id, field)
}

function handleExport() {
  // 导出筛选后的所有数据
  const data = props.data
  const keyword = debouncedSearchText.value.trim()
  const hasKeyword = keyword !== ''
  const hasFilters = Object.keys(columnFilters.value).length > 0

  let exportData = data
  if (hasKeyword || hasFilters) {
    const filteredIds = getFilteredIds()
    exportData = data.filter(r => filteredIds.has(r.id))
  }

  if (exportData.length === 0) return

  if (sortField.value) {
    exportData = sortData(exportData, sortField.value, sortDirection.value, visibleFields.value)
  }

  exportToExcel(exportData, visibleFields.value, '数据导出')
  emit('export', exportData)
}

// 数据变化时重置页码和缓存
watch(() => props.data, () => {
  currentPage.value = 1
  // 清除所有缓存
  filterResultCache.keyword = ''
  filterResultCache.filters = {}
  filterResultCache.filteredIds.clear()
  filterResultCache.version = 0
  sortResultCache.field = ''
  sortResultCache.sortedData = null
}, { deep: false })

defineExpose({ clearAllFilters, tableRef })
</script>

<style scoped lang="scss">
.excel-view {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.excel-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  gap: 16px;

  .toolbar-left,
  .toolbar-right {
    display: flex;
    align-items: center;
    gap: 12px;
  }
}

.excel-table-wrapper {
  flex: 1;
  overflow: hidden;
}

.excel-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  border-top: 1px solid #ebeef5;

  .status-info {
    font-size: 13px;
    color: #909399;
  }
}

.column-header {
  display: inline-flex;
  align-items: center;
  gap: 4px;

  .filter-icon {
    cursor: pointer;
    font-size: 14px;
    color: #c0c4cc;
    transition: color 0.2s;

    &:hover { color: #409eff; }
    &.active { color: #409eff; }
  }
}

.filter-dialog-content {
  padding: 8px 0;
}

// 单元格样式
:deep(.cell-tags) {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

:deep(.cell-tag) {
  display: inline-block;
  padding: 0 6px;
  height: 20px;
  line-height: 18px;
  font-size: 12px;
  border: 1px solid #d9ecff;
  border-radius: 4px;
  background-color: #ecf5ff;
  color: #409eff;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    color: #409eff;
    border-color: #409eff;
  }
}

:deep(.cell-tag-more) {
  background-color: #f4f4f5;
  border-color: #e9e9eb;
  color: #909399;
}

:deep(.cell-link) {
  color: #409eff;
  cursor: pointer;

  &:hover { text-decoration: underline; }
}
</style>