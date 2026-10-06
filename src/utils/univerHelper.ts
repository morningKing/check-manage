/**
 * Univer Sheet 数据转换工具
 *
 * 将 FieldConfig[] + DynamicRecord[] 转换为 Univer 的 IWorkbookData 格式
 */
import { LocaleType } from '@univerjs/core'
import type { IWorkbookData } from '@univerjs/core'
import type { FieldConfig, DynamicRecord } from '@/types'

/**
 * 列宽度映射（像素）
 */
const COLUMN_WIDTH_MAP: Record<string, number> = {
  textarea: 200,
  date: 150,
  datetime: 170,
  autoTimestamp: 170,
  autoSequence: 120,
  select: 120,
  multiSelect: 140,
  reference: 120,
  relation: 180,
  quoteSelect: 180,
  richText: 200,
  default: 150
}

/**
 * 获取列宽度
 */
function getColumnWidth(field: FieldConfig): number {
  return COLUMN_WIDTH_MAP[field.controlType] || COLUMN_WIDTH_MAP.default
}

/**
 * 格式化单元格显示值（导出供测试使用）
 */
export function formatCellValue(value: any, field: FieldConfig, record: DynamicRecord): string {
  if (value === null || value === undefined || value === '') return ''

  switch (field.controlType) {
    case 'select':
    case 'radio':
      return field.options?.find(o => o.value === value)?.label || String(value)

    case 'multiSelect':
    case 'checkbox':
      if (Array.isArray(value)) {
        return value
          .map(v => field.options?.find(o => o.value === v)?.label || String(v))
          .join('、')
      }
      return String(value)

    case 'relation': {
      const labels = record[`_rel_${field.fieldName}_labels`]
      if (Array.isArray(labels) && labels.length > 0) {
        return labels.map((item: any) => item.label).join('、')
      }
      return ''
    }

    case 'reference': {
      const display = record[`_ref_${field.fieldName}_display`]
      return display ? String(display) : String(value)
    }

    case 'quoteSelect': {
      const labels = record[`_quote_${field.fieldName}_labels`]
      if (Array.isArray(labels) && labels.length > 0) {
        return labels.map((item: any) => item.label).join('、')
      }
      return ''
    }

    case 'date':
      return formatDateStr(value, false)

    case 'datetime':
    case 'autoTimestamp':
      return formatDateStr(value, true)

    case 'richText':
      // 去除 HTML 标签
      return String(value).replace(/<[^>]*>/g, '')

    case 'file':
    case 'image':
      return Array.isArray(value) ? `${value.length} 个文件` : ''

    default:
      return String(value)
  }
}

/**
 * 格式化日期字符串
 */
function formatDateStr(value: string, withTime: boolean): string {
  if (!value) return ''
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

/**
 * 构建 Univer Workbook 数据
 *
 * @param fields - 字段配置列表
 * @param data - 数据记录列表
 * @returns Univer IWorkbookData
 */
export function buildWorkbookData(
  fields: FieldConfig[],
  data: DynamicRecord[]
): IWorkbookData {
  // 过滤可见字段并排序
  const visibleFields = fields
    .filter(f => !f.hidden)
    .sort((a, b) => a.order - b.order)

  // 构建列配置（序号列 + 数据列），使用 IColumnData 的 w 属性
  const columns: Record<number, { w: number }> = {}
  columns[0] = { w: 60 } // 序号列
  visibleFields.forEach((field, index) => {
    columns[index + 1] = { w: getColumnWidth(field) }
  })

  // 构建单元格数据
  const cellData: Record<number, Record<number, { v: string | number; s?: string }>> = {}

  // 第 0 行：表头
  cellData[0] = {}
  cellData[0][0] = { v: '#', s: 'header' } // 序号列表头
  visibleFields.forEach((field, index) => {
    cellData[0][index + 1] = { v: field.label, s: 'header' }
  })

  // 第 1..N 行：数据行
  data.forEach((record, rowIndex) => {
    const row = rowIndex + 1
    cellData[row] = {}

    // 序号列
    cellData[row][0] = { v: row }

    // 数据列
    visibleFields.forEach((field, colIndex) => {
      const col = colIndex + 1
      const value = record[field.fieldName]
      const displayValue = formatCellValue(value, field, record)
      cellData[row][col] = { v: displayValue || '' }
    })
  })

  // 构建样式
  const styles: Record<string, any> = {
    header: {
      bl: 1, // bold
      bg: { rgb: '#F5F5F5' } // 灰色背景
    }
  }

  // 构建 Workbook 数据
  const workbookData: IWorkbookData = {
    id: 'excel-view-workbook',
    name: '数据视图',
    appVersion: '0.16.1',
    locale: LocaleType.ZH_CN,
    styles,
    sheetOrder: ['sheet-1'],
    sheets: {
      'sheet-1': {
        id: 'sheet-1',
        name: 'Sheet1',
        rowCount: data.length + 1,
        columnCount: visibleFields.length + 1,
        cellData,
        columnData: columns,
        freeze: {
          xSplit: 1, // 冻结 1 列（序号列）
          ySplit: 1, // 冻结 1 行（表头）
          startRow: 1,
          startColumn: 1
        },
        defaultColumnWidth: 150,
        defaultRowHeight: 24
      }
    }
  }

  return workbookData
}

// ==================== Excel 单元格编辑回写（TD-A18b） ====================

/**
 * 允许在 Excel 视图中直接编辑回写的字段控制类型（标量字段）。
 *
 * 其余类型（multiSelect/checkbox/relation/reference/quoteSelect/richText/
 * markdown/file/image/compositeText/autoSequence/autoTimestamp/statusBadge）
 * 的单元格文本是「派生展示」——标签串、文件计数、去标签摘要、拼接/自增/
 * 时间戳等，无法把编辑后的文本忠实回转成原始字段值，贸然写回会破坏数据；
 * 因此这些列始终保持只读（BeforeSheetEditStart 拦截，双击仍走导航事件）。
 */
const EDITABLE_CONTROL_TYPES: ReadonlySet<string> = new Set([
  'text',
  'textarea',
  'number',
  'radio',
  'select',
  'date',
  'datetime'
])

/**
 * 该字段控制类型是否允许 Excel 单元格编辑回写
 */
export function isEditableControlType(controlType: string): boolean {
  return EDITABLE_CONTROL_TYPES.has(controlType)
}

/** parseCellEdit 结果：ok=false 时 reason 用于 ElMessage 警告文案 */
export type CellEditParseResult =
  | { ok: true; value: string | number }
  | { ok: false; reason: string }

/**
 * 把单元格编辑后的显示文本回转为字段的原始值（显示↔原始 的映射）。
 *
 * 规则：
 * - 空文本 → 清空字段（value ''）
 * - number → Number(text)，NaN 拒绝（绝不猜值）
 * - select/radio → 选项 label→value（直接输入 option value 也接受）；
 *   匹配不到任何选项时拒绝，不发明值
 * - text/textarea/date/datetime → 字符串原样（date 列显示格式即存储格式）
 *
 * @param text - 编辑后的单元格文本
 * @param field - 目标字段配置
 */
export function parseCellEdit(text: string, field: FieldConfig): CellEditParseResult {
  if (text === '') return { ok: true, value: '' }

  switch (field.controlType) {
    case 'number': {
      const num = Number(text)
      if (Number.isNaN(num)) {
        return { ok: false, reason: `「${field.label}」列需要数字，已取消回写` }
      }
      return { ok: true, value: num }
    }

    case 'select':
    case 'radio': {
      const option = field.options?.find(o => o.label === text || o.value === text)
      if (!option) {
        return { ok: false, reason: `「${field.label}」没有选项「${text}」，已取消回写` }
      }
      // FieldOption.value 允许 boolean，选项单元格文本回转只会得到 string/number
      return { ok: true, value: option.value as string | number }
    }

    default:
      return { ok: true, value: text }
  }
}

/**
 * 单元格坐标 → 记录/字段 映射。
 *
 * 与 buildWorkbookData 的布局一一对应：第 0 行是表头、第 0 列是序号列，
 * 数据行 = rowIndex - 1，数据列 = 可见字段（filter hidden + sort order）
 * 中的第 colIndex - 1 个。表头/序号列/越界 返回 null（不可编辑）。
 */
export function cellToTarget(
  rowIndex: number,
  colIndex: number,
  fields: FieldConfig[],
  records: DynamicRecord[]
): { record: DynamicRecord; field: FieldConfig } | null {
  if (rowIndex <= 0 || colIndex <= 0) return null

  const visibleFields = fields
    .filter(f => !f.hidden)
    .sort((a, b) => a.order - b.order)

  const field = visibleFields[colIndex - 1]
  const record = records[rowIndex - 1]
  if (!field || !record) return null

  return { record, field }
}
