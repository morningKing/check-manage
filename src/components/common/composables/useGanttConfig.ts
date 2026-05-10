// src/components/common/composables/useGanttConfig.ts
import type { GanttConfig, FieldConfig, DynamicRecord } from '@/types'

/**
 * 状态颜色映射表（复用 CalendarConfig）
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

export function getColorForValue(value: any): string {
  if (value == null) return '#409EFF'
  return colorMap[String(value).toLowerCase()] || '#409EFF'
}

/**
 * 将日期字符串转换为时间戳
 */
export function dateToTimestamp(dateStr: string | Date | null): number {
  if (!dateStr) return 0
  const d = typeof dateStr === 'string' ? new Date(dateStr) : dateStr
  return d.getTime()
}

/**
 * 甘特图任务数据
 */
export interface GanttTask {
  id: string
  name: string
  startDate: number  // 时间戳
  endDate: number    // 时间戳
  progress: number   // 0-100
  color: string
  dependencies: string[]  // 依赖任务 ID 列表
  record: DynamicRecord   // 原始记录
}

/**
 * 将 DynamicRecord 转换为 GanttTask
 */
export function transformToGanttTasks(
  records: DynamicRecord[],
  config: GanttConfig,
  _fields: FieldConfig[]
): GanttTask[] {
  return records
    .filter(r => r[config.startDateField] && r[config.endDateField])
    .map(r => {
      const colorValue = config.colorField ? r[config.colorField] : null
      const progressValue = config.progressField ? Number(r[config.progressField]) || 0 : 0
      const depsValue = config.dependenciesField ? r[config.dependenciesField] : null
      const dependencies = Array.isArray(depsValue)
        ? depsValue.filter(id => records.some(rec => rec.id === id))
        : []

      return {
        id: r.id,
        name: r[config.titleField] || '无标题',
        startDate: dateToTimestamp(r[config.startDateField]),
        endDate: dateToTimestamp(r[config.endDateField]),
        progress: Math.min(100, Math.max(0, progressValue)),
        color: getColorForValue(colorValue),
        dependencies,
        record: r
      }
    })
    .sort((a, b) => a.startDate - b.startDate)
}

/**
 * 验证甘特图配置是否有效
 */
export function validateGanttConfig(
  config: GanttConfig | undefined,
  fields: FieldConfig[]
): { valid: boolean; reason?: string } {
  if (!config) {
    return { valid: false, reason: '缺少甘特图配置' }
  }

  if (!config.startDateField) {
    return { valid: false, reason: '缺少开始日期字段配置' }
  }

  if (!config.endDateField) {
    return { valid: false, reason: '缺少结束日期字段配置' }
  }

  if (!config.titleField) {
    return { valid: false, reason: '缺少标题字段配置' }
  }

  const startField = fields.find(f => f.fieldName === config.startDateField)
  if (!startField || !['date', 'datetime'].includes(startField.controlType)) {
    return { valid: false, reason: `开始日期字段 ${config.startDateField} 不是日期类型` }
  }

  const endField = fields.find(f => f.fieldName === config.endDateField)
  if (!endField || !['date', 'datetime'].includes(endField.controlType)) {
    return { valid: false, reason: `结束日期字段 ${config.endDateField} 不是日期类型` }
  }

  return { valid: true }
}

/**
 * Composable: 甘特图配置处理
 */
export function useGanttConfig(
  config: GanttConfig | undefined,
  fields: FieldConfig[],
  records: DynamicRecord[]
) {
  const isValid = validateGanttConfig(config, fields)

  const tasks = isValid.valid
    ? transformToGanttTasks(records, config!, fields)
    : []

  // 计算时间范围
  const minDate = tasks.length > 0 ? Math.min(...tasks.map(t => t.startDate)) : Date.now()
  const maxDate = tasks.length > 0 ? Math.max(...tasks.map(t => t.endDate)) : Date.now() + 7 * 24 * 3600 * 1000

  return {
    isValid,
    tasks,
    minDate,
    maxDate,
    getColorForValue,
    dateToTimestamp
  }
}