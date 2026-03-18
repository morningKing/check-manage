/**
 * Excel 视图组件 - 基于 Univer Sheet
 *
 * 职责：
 * - 以真正的电子表格形式展示数据（工具栏、公式栏、Sheet 标签）
 * - 只读模式，不可编辑
 * - 支持列筛选、列宽拖拽、冻结窗格
 * - 双击单元格触发导航事件
 * - 支持跨路由状态缓存（筛选、样式等）
 */
<template>
  <div class="excel-view">
    <!-- Univer 容器 -->
    <div ref="univerContainerRef" class="univer-container"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { createUniver, LocaleType } from '@univerjs/presets'
import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core'
import { UniverSheetsFilterPreset } from '@univerjs/preset-sheets-filter'
import type { FieldConfig, DynamicRecord } from '@/types'
import { buildWorkbookData } from '@/utils/univerHelper'

// 导入 Univer 样式
import '@univerjs/preset-sheets-core/lib/index.css'
import '@univerjs/preset-sheets-filter/lib/index.css'
import '@univerjs/design/lib/index.css'

// 导入中文语言包
// @ts-ignore - locale files exist at runtime
import sheetsCoreZhCN from '@univerjs/preset-sheets-core/lib/locales/zh-CN'
// @ts-ignore - locale files exist at runtime
import sheetsFilterZhCN from '@univerjs/preset-sheets-filter/lib/locales/zh-CN'

// ==================== 全局快照缓存 ====================

interface CacheEntry {
  snapshot: any
  dataHash: string
}

/** 按 collection 缓存 Excel 快照 */
const excelSnapshotCache = new Map<string, CacheEntry>()

/** 生成数据 hash（简单基于记录数和第一条数据） */
function generateDataHash(data: DynamicRecord[]): string {
  if (data.length === 0) return 'empty'
  const first = data[0]
  return `${data.length}-${first.id || 'no-id'}`
}

// ==================== Props & Emits ====================

interface Props {
  /** 表格数据 */
  data: DynamicRecord[]
  /** 字段配置列表 */
  fields: FieldConfig[]
  /** 加载状态 */
  loading?: boolean
  /** 集合 ID，用于缓存快照 */
  collectionId?: string
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  collectionId: ''
})

const emit = defineEmits<{
  (e: 'row-click', row: DynamicRecord): void
  (e: 'reference-click', row: DynamicRecord, field: FieldConfig): void
  (e: 'relation-click', relatedRecordId: string, field: FieldConfig): void
  (e: 'quote-click', quotedRecordId: string, field: FieldConfig): void
}>()

// ==================== State ====================

const univerContainerRef = ref<HTMLElement>()
// @ts-ignore used for cleanup reference
let univerInstance: any = null // eslint-disable-line @typescript-eslint/no-unused-vars
let univerAPI: any = null
let currentDataHash = ''

// ==================== Lifecycle ====================

onMounted(() => {
  if (univerContainerRef.value) {
    initUniver()
  }
})

onBeforeUnmount(() => {
  // 保存快照到缓存
  saveSnapshotToCache()
  disposeUniver()
})

// 监听数据变化，重新加载 workbook（仅当数据实际变化时）
watch(() => props.data, (newData) => {
  if (!univerAPI) return

  const newHash = generateDataHash(newData)
  // 如果数据 hash 变化，才重新加载
  if (newHash !== currentDataHash) {
    currentDataHash = newHash
    reloadWorkbook()
  }
}, { deep: false })

// ==================== Methods ====================

/**
 * 初始化 Univer 实例
 */
function initUniver() {
  if (!univerContainerRef.value) return

  const { univer: _univer, univerAPI: api } = createUniver({
    locale: LocaleType.ZH_CN,
    locales: {
      [LocaleType.ZH_CN]: {
        ...sheetsCoreZhCN,
        ...sheetsFilterZhCN
      }
    },
    presets: [
      UniverSheetsCorePreset({
        container: univerContainerRef.value,
        header: true,
        toolbar: true,
        footer: { sheetBar: true, statisticBar: true },
        formulaBar: false,
      }),
      UniverSheetsFilterPreset()
    ]
  })

  univerInstance = _univer
  univerAPI = api

  // 加载初始数据（优先使用缓存）
  loadWorkbook()

  // 设置只读模式
  setReadOnly()

  // 监听单元格双击事件
  setupCellClickListener()
}

/**
 * 加载 Workbook 数据
 */
function loadWorkbook() {
  if (!univerAPI) return

  const dataHash = generateDataHash(props.data)
  currentDataHash = dataHash

  // 检查是否有缓存
  const cached = props.collectionId ? excelSnapshotCache.get(props.collectionId) : null

  if (cached && cached.dataHash === dataHash) {
    // 数据未变化，恢复快照（保留筛选、样式等）
    univerAPI.createWorkbook(cached.snapshot)
  } else {
    // 数据变化或无缓存，创建新 workbook
    const workbookData = buildWorkbookData(props.fields, props.data)
    univerAPI.createWorkbook(workbookData)
  }
}

/**
 * 保存快照到缓存
 */
function saveSnapshotToCache() {
  if (!univerAPI || !props.collectionId) return

  const activeWorkbook = univerAPI.getActiveWorkbook()
  if (!activeWorkbook) return

  try {
    const snapshot = activeWorkbook.save()
    excelSnapshotCache.set(props.collectionId, {
      snapshot,
      dataHash: currentDataHash
    })
  } catch (e) {
    console.warn('Failed to save Excel snapshot:', e)
  }
}

/**
 * 重新加载 Workbook（数据变化时）
 */
function reloadWorkbook() {
  if (!univerAPI) return

  // 销毁旧的 workbook
  const activeWorkbook = univerAPI.getActiveWorkbook()
  if (activeWorkbook) {
    univerAPI.disposeUnit(activeWorkbook.getId())
  }

  // 加载新数据
  loadWorkbook()
  setReadOnly()
}

/**
 * 设置只读模式
 */
function setReadOnly() {
  if (!univerAPI) return

  // 监听编辑开始事件，阻止所有编辑操作
  univerAPI.addEvent(univerAPI.Event.BeforeSheetEditStart, (params: any) => {
    params.cancel = true // 阻止编辑
  })
}

/**
 * 监听单元格双击事件
 * 使用 DOM dblclick + Univer 选区来确定双击的单元格
 */
function setupCellClickListener() {
  if (!univerAPI || !univerContainerRef.value) return

  univerContainerRef.value.addEventListener('dblclick', handleCellDoubleClick)
}

/**
 * 处理单元格双击
 */
function handleCellDoubleClick() {
  if (!univerAPI) return

  const activeWorkbook = univerAPI.getActiveWorkbook()
  if (!activeWorkbook) return

  const activeSheet = activeWorkbook.getActiveSheet()
  if (!activeSheet) return

  // 获取当前选中的单元格
  const selection = activeSheet.getSelection()
  if (!selection) return

  const range = selection.getActiveRange()
  if (!range) return

  const row = range.getRow()
  const col = range.getColumn()

  // 第 0 行是表头，跳过
  if (row === 0) return

  // 第 0 列是序号列，双击触发 row-click
  if (col === 0) {
    const dataIndex = row - 1
    if (dataIndex >= 0 && dataIndex < props.data.length) {
      emit('row-click', props.data[dataIndex])
    }
    return
  }

  // 数据列：根据字段类型触发不同事件
  const visibleFields = props.fields
    .filter(f => !f.hidden)
    .sort((a, b) => a.order - b.order)

  const fieldIndex = col - 1
  if (fieldIndex < 0 || fieldIndex >= visibleFields.length) return

  const field = visibleFields[fieldIndex]
  const dataIndex = row - 1
  if (dataIndex < 0 || dataIndex >= props.data.length) return

  const record = props.data[dataIndex]

  // 根据字段类型触发事件
  if (field.controlType === 'relation') {
    const labels = record[`_rel_${field.fieldName}_labels`]
    if (Array.isArray(labels) && labels.length > 0) {
      // 双击关联字段，跳转到第一个关联记录
      emit('relation-click', labels[0].id, field)
    }
  } else if (field.controlType === 'reference') {
    emit('reference-click', record, field)
  } else if (field.controlType === 'quoteSelect') {
    const labels = record[`_quote_${field.fieldName}_labels`]
    if (Array.isArray(labels) && labels.length > 0) {
      // 双击引用选择字段，跳转到第一个引用记录
      emit('quote-click', labels[0].id, field)
    }
  } else {
    // 其他字段：双击打开详情
    emit('row-click', record)
  }
}

/**
 * 销毁 Univer 实例
 */
function disposeUniver() {
  if (univerContainerRef.value) {
    univerContainerRef.value.removeEventListener('dblclick', handleCellDoubleClick)
  }

  univerInstance = null
  univerAPI = null
}

// 暴露方法（保持接口兼容）
defineExpose({
  clearAllFilters: () => {
    // Univer 的筛选通过 UI 操作，这里不需要实现
  },
  /** 手动保存快照（供父组件在视图切换时调用） */
  saveSnapshot: saveSnapshotToCache
})
</script>

<style scoped lang="scss">
.excel-view {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.univer-container {
  flex: 1;
  overflow: hidden;
  position: relative;

  // 禁用编辑（只读模式）
  :deep(.univer-sheet-container) {
    pointer-events: auto;
  }

  // 隐藏编辑相关的 UI 元素
  :deep(.univer-formula-bar) {
    display: none;
  }
}
</style>
