// src/components/common/composables/__tests__/useCalendarConfig.test.ts
import { describe, it, expect } from 'vitest'
import {
  getColorForValue,
  formatDateToISO,
  transformToEvents,
  validateCalendarConfig
} from '../useCalendarConfig'
import type { CalendarConfig, FieldConfig, DynamicRecord } from '@/types'

describe('useCalendarConfig', () => {
  describe('getColorForValue', () => {
    it('returns correct color for known status values', () => {
      expect(getColorForValue('pending')).toBe('#E6A23C')
      expect(getColorForValue('进行中')).toBe('#409EFF')
      expect(getColorForValue('已完成')).toBe('#67C23A')
      expect(getColorForValue('高')).toBe('#F56C6C')
    })

    it('returns default color for unknown values', () => {
      expect(getColorForValue('unknown')).toBe('#409EFF')
      expect(getColorForValue(null)).toBe('#409EFF')
    })
  })

  describe('formatDateToISO', () => {
    it('formats date to YYYY-MM-DD', () => {
      const date = new Date('2026-05-09T12:00:00Z')
      expect(formatDateToISO(date)).toBe('2026-05-09')
    })
  })

  describe('transformToEvents', () => {
    const config: CalendarConfig = {
      dateField: 'startDate',
      endDateField: 'endDate',
      cardTitle: 'title',
      cardColorField: 'status'
    }

    const fields: FieldConfig[] = [
      { id: '1', fieldName: 'startDate', label: '开始日期', controlType: 'date', required: false, order: 1 },
      { id: '2', fieldName: 'endDate', label: '结束日期', controlType: 'date', required: false, order: 2 },
      { id: '3', fieldName: 'title', label: '标题', controlType: 'text', required: false, order: 3 },
      { id: '4', fieldName: 'status', label: '状态', controlType: 'select', required: false, order: 4, options: [
        { label: '进行中', value: 'progress' }
      ]}
    ]

    const records: DynamicRecord[] = [
      { id: '1', title: '任务A', startDate: '2026-05-01', endDate: '2026-05-05', status: 'progress' },
      { id: '2', title: '任务B', startDate: '2026-05-10', status: 'pending' },
      { id: '3', title: '任务C', startDate: null },
    ]

    it('filters records without date', () => {
      const events = transformToEvents(records, config, fields)
      expect(events.length).toBe(2)
    })

    it('generates correct event structure', () => {
      const events = transformToEvents(records, config, fields)
      const event1 = events.find(e => e.id === '1')

      expect(event1?.title).toBe('任务A')
      expect(event1?.start).toBe('2026-05-01')
      // end date should be +1 day (FullCalendar exclusive)
      expect(event1?.end).toBe('2026-05-06')
      expect(event1?.backgroundColor).toBe('#409EFF')
    })

    it('handles single-day events without endDateField', () => {
      const singleConfig: CalendarConfig = {
        dateField: 'startDate',
        cardTitle: 'title'
      }
      const events = transformToEvents(records.filter(r => r.id === '2'), singleConfig, fields)

      expect(events[0].end).toBeUndefined()
    })
  })

  describe('validateCalendarConfig', () => {
    const fields: FieldConfig[] = [
      { id: '1', fieldName: 'startDate', label: '开始日期', controlType: 'date', required: false, order: 1 },
      { id: '2', fieldName: 'title', label: '标题', controlType: 'text', required: false, order: 3 },
    ]

    it('returns invalid when config is missing', () => {
      const result = validateCalendarConfig(undefined, fields)
      expect(result.valid).toBe(false)
      expect(result.reason).toBe('缺少日历配置')
    })

    it('returns invalid when dateField is missing', () => {
      const config: CalendarConfig = { dateField: '', cardTitle: 'title' }
      const result = validateCalendarConfig(config, fields)
      expect(result.valid).toBe(false)
      expect(result.reason).toBe('缺少日期字段配置')
    })

    it('returns invalid when dateField is not date type', () => {
      const config: CalendarConfig = { dateField: 'title', cardTitle: 'title' }
      const result = validateCalendarConfig(config, fields)
      expect(result.valid).toBe(false)
      expect(result.reason).toContain('不是日期类型')
    })

    it('returns valid for correct config', () => {
      const config: CalendarConfig = { dateField: 'startDate', cardTitle: 'title' }
      const result = validateCalendarConfig(config, fields)
      expect(result.valid).toBe(true)
    })
  })
})