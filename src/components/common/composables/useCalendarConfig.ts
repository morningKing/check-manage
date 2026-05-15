// src/components/common/composables/useCalendarConfig.ts
import type { CalendarConfig, FieldConfig, DynamicRecord } from '@/types'
import type { EventInput } from '@fullcalendar/core'

/**
 * 状态颜色映射表
 * 复用 KanbanBoard 的颜色逻辑
 */
const colorMap: Record<string, string> = {
  pending: '#E6A23C',   '待处理': '#E6A23C',
  progress: '#409EFF',  '进行中': '#409EFF',
  done: '#67C23A',      '已完成': '#67C23A',
  blocked: '#F56C6C',   '阻塞': '#F56C6C',
  high: '#F56C6C',      '高': '#F56C6C',   '紧急': '#F56C6C',
  medium: '#E6A23C',    '中': '#E6A23C',   '一般': '#E6A23C',
  low: '#67C23A',       '低': '#67C23A',
}

/**
 * 获取颜色值
 */
export function getColorForValue(value: any): string {
  if (value == null) return '#409EFF'
  return colorMap[String(value).toLowerCase()] || '#409EFF'
}

/**
 * FullCalendar 的 end 日期是排他性的
 * 需要加一天才能正确显示跨天事件
 */
function addDay(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00') // 使用本地时间解析，避免 UTC 偏移
  d.setDate(d.getDate() + 1)
  return formatDateToISO(d)
}

/**
 * 格式化日期为 YYYY-MM-DD 格式（使用本地时间）
 */
export function formatDateToISO(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/**
 * 将 DynamicRecord 转换为 FullCalendar Event Object
 */
export function transformToEvents(
  records: DynamicRecord[],
  config: CalendarConfig,
  _fields: FieldConfig[]
): EventInput[] {
  return records
    .filter(r => r[config.dateField])  // 必须有日期
    .map(r => {
      const colorValue = config.cardColorField ? r[config.cardColorField] : null
      const bgColor = getColorForValue(colorValue)

      const endValue = config.endDateField ? r[config.endDateField] : null
      const endDate = endValue ? addDay(String(endValue)) : undefined

      return {
        id: r.id,
        title: r[config.cardTitle] || '无标题',
        start: r[config.dateField],
        end: endDate,
        backgroundColor: bgColor,
        borderColor: bgColor,
        textColor: '#303133',
        extendedProps: {
          record: r,
          colorField: config.cardColorField,
          colorValue: colorValue
        }
      }
    })
}

/**
 * 验证日历配置是否有效
 */
export function validateCalendarConfig(
  config: CalendarConfig | undefined,
  fields: FieldConfig[]
): { valid: boolean; reason?: string } {
  if (!config) {
    return { valid: false, reason: '缺少日历配置' }
  }

  if (!config.dateField) {
    return { valid: false, reason: '缺少日期字段配置' }
  }

  const dateField = fields.find(f => f.fieldName === config.dateField)
  if (!dateField) {
    return { valid: false, reason: `日期字段 ${config.dateField} 不存在` }
  }

  if (!['date', 'datetime'].includes(dateField.controlType)) {
    return { valid: false, reason: `字段 ${config.dateField} 不是日期类型` }
  }

  if (!config.cardTitle) {
    return { valid: false, reason: '缺少标题字段配置' }
  }

  return { valid: true }
}

/**
 * Composable: 日历配置处理
 */
export function useCalendarConfig(
  config: CalendarConfig | undefined,
  fields: FieldConfig[],
  records: DynamicRecord[]
) {
  const isValid = validateCalendarConfig(config, fields)

  const events = isValid.valid
    ? transformToEvents(records, config!, fields)
    : []

  const canResize = config?.endDateField ? true : false

  return {
    isValid,
    events,
    canResize,
    getColorForValue,
    formatDateToISO
  }
}