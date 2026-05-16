import { describe, expect, it } from 'vitest'
import type { AggregateResult } from '@/api/dashboard'
import {
  getDefaultMetricKey,
  getMetricValue,
  toCartesianChartModel,
  toPieChartData,
  toTableModel,
} from '../aggregateResult'

describe('aggregateResult helpers', () => {
  describe('getDefaultMetricKey', () => {
    it('returns undefined for null/undefined result', () => {
      expect(getDefaultMetricKey(null)).toBeUndefined()
      expect(getDefaultMetricKey(undefined)).toBeUndefined()
    })

    it('returns first metric key for single result', () => {
      const result: AggregateResult = {
        type: 'single',
        value: 10,
        metrics: { count: 10, sum: 50 },
      }
      expect(getDefaultMetricKey(result)).toBe('count')
    })

    it('returns first metric key from first grouped row', () => {
      const result: AggregateResult = {
        type: 'grouped',
        data: [
          { key: 'V1.0', value: 5, metrics: { records: 5, bugs: 7 } },
          { key: 'V2.0', value: 3, metrics: { records: 3, bugs: 4 } },
        ],
      }
      expect(getDefaultMetricKey(result)).toBe('records')
    })
  })

  describe('getMetricValue', () => {
    it('returns value when no metrics present', () => {
      expect(getMetricValue({ value: 42 })).toBe(42)
    })

    it('returns specific metric value when key matches', () => {
      expect(getMetricValue({ value: 42, metrics: { bugs: 7, count: 5 } }, 'bugs')).toBe(7)
    })

    it('returns first metric value when key not found', () => {
      expect(getMetricValue({ value: 42, metrics: { bugs: 7 } }, 'unknown')).toBe(7)
    })

    it('returns 0 for null/undefined values', () => {
      expect(getMetricValue({ value: null as any, metrics: {} })).toBe(0)
      expect(getMetricValue({ value: undefined as any })).toBe(0)
    })
  })

  describe('toCartesianChartModel', () => {
    it('returns empty model for null/undefined result', () => {
      expect(toCartesianChartModel(null)).toEqual({ categories: [], series: [] })
      expect(toCartesianChartModel(undefined)).toEqual({ categories: [], series: [] })
    })

    it('returns empty model for single result', () => {
      const result: AggregateResult = { type: 'single', value: 10 }
      expect(toCartesianChartModel(result)).toEqual({ categories: [], series: [] })
    })

    it('builds multi-series cartesian data from grouped metrics', () => {
      const result: AggregateResult = {
        type: 'grouped',
        data: [
          { key: 'open', value: 3, metrics: { records: 3, totalScore: 15 } },
          { key: 'closed', value: 1, metrics: { records: 1, totalScore: 8 } },
        ],
      }

      const model = toCartesianChartModel(result)

      expect(model.categories).toEqual(['open', 'closed'])
      expect(model.series).toEqual([
        { name: 'records', data: [3, 1] },
        { name: 'totalScore', data: [15, 8] },
      ])
    })

    it('builds single series when metricKey is specified', () => {
      const result: AggregateResult = {
        type: 'grouped',
        data: [
          { key: 'V1.0', value: 5, metrics: { records: 5, bugs: 7 } },
          { key: 'V2.0', value: 3, metrics: { records: 3, bugs: 4 } },
        ],
      }

      const model = toCartesianChartModel(result, 'bugs')

      expect(model.series).toHaveLength(1)
      expect(model.series[0].name).toBe('bugs')
      expect(model.series[0].data).toEqual([7, 4])
    })

    it('builds matrix series using rows as x-axis and columns as series', () => {
      const result: AggregateResult = {
        type: 'matrix',
        rows: ['open', 'closed'],
        columns: ['high', 'low'],
        data: [
          { rowKey: 'open', columnKey: 'high', value: 3, metrics: { records: 3, totalScore: 15 } },
          { rowKey: 'open', columnKey: 'low', value: 2, metrics: { records: 2, totalScore: 6 } },
          { rowKey: 'closed', columnKey: 'high', value: 1, metrics: { records: 1, totalScore: 8 } },
        ],
      }

      const model = toCartesianChartModel(result, 'totalScore')

      expect(model.categories).toEqual(['open', 'closed'])
      expect(model.series).toEqual([
        { name: 'high', data: [15, 8] },
        { name: 'low', data: [6, 0] },
      ])
    })

    it('handles sparse matrix data with zero fill', () => {
      const result: AggregateResult = {
        type: 'matrix',
        rows: ['open', 'closed'],
        columns: ['high', 'low'],
        data: [
          { rowKey: 'open', columnKey: 'high', value: 3 },
          // missing: open/low, closed/high
          { rowKey: 'closed', columnKey: 'low', value: 4 },
        ],
      }
      const model = toCartesianChartModel(result)
      expect(model.series[0].data).toEqual([3, 0])
      expect(model.series[1].data).toEqual([0, 4])
    })
  })

  describe('toPieChartData', () => {
    it('returns empty array for null/undefined result', () => {
      expect(toPieChartData(null)).toEqual([])
      expect(toPieChartData(undefined)).toEqual([])
    })

    it('converts single result to single slice', () => {
      const result: AggregateResult = { type: 'single', value: 10 }
      const data = toPieChartData(result, 'count')
      expect(data).toEqual([{ name: 'count', value: 10 }])
    })

    it('converts grouped result to slices', () => {
      const result: AggregateResult = {
        type: 'grouped',
        data: [
          { key: 'fatal', value: 3 },
          { key: 'major', value: 5 },
          { key: 'minor', value: 2 },
        ],
      }
      const data = toPieChartData(result)
      expect(data).toEqual([
        { name: 'fatal', value: 3 },
        { name: 'major', value: 5 },
        { name: 'minor', value: 2 },
      ])
    })

    it('converts matrix result to combined labels', () => {
      const result: AggregateResult = {
        type: 'matrix',
        rows: ['open', 'closed'],
        columns: ['high', 'low'],
        data: [
          { rowKey: 'open', columnKey: 'high', value: 3 },
          { rowKey: 'closed', columnKey: 'low', value: 4 },
        ],
      }
      const data = toPieChartData(result)
      expect(data).toEqual([
        { name: 'open / high', value: 3 },
        { name: 'closed / low', value: 4 },
      ])
    })
  })

  describe('toTableModel', () => {
    it('returns empty model for null/undefined result', () => {
      expect(toTableModel(null)).toEqual({ columns: [], rows: [] })
      expect(toTableModel(undefined)).toEqual({ columns: [], rows: [] })
    })

    it('flattens matrix data for table rendering', () => {
      const result: AggregateResult = {
        type: 'matrix',
        rows: ['open'],
        columns: ['high'],
        data: [
          { rowKey: 'open', columnKey: 'high', value: 3, metrics: { records: 3, totalScore: 15 } },
        ],
      }

      const table = toTableModel(result, {
        groupLabel: '状态',
        columnLabel: '等级',
        metricLabels: { records: '记录数', totalScore: '总分' },
      })

      expect(table.columns.map(column => column.label)).toEqual(['状态', '等级', '记录数', '总分'])
      expect(table.rows).toEqual([{ rowKey: 'open', columnKey: 'high', records: 3, totalScore: 15 }])
    })

    it('converts single result with metrics', () => {
      const result: AggregateResult = {
        type: 'single',
        value: 10,
        metrics: { records: 10, bugs: 7 },
      }
      const model = toTableModel(result, { metricLabels: { records: '需求数', bugs: '问题数' } })
      expect(model.columns).toEqual([
        { key: 'records', label: '需求数', align: 'right' },
        { key: 'bugs', label: '问题数', align: 'right' },
      ])
      expect(model.rows).toEqual([{ records: 10, bugs: 7 }])
    })

    it('converts single result without metrics', () => {
      const result: AggregateResult = { type: 'single', value: 10 }
      const model = toTableModel(result, { valueLabel: '总数' })
      expect(model.columns).toEqual([{ key: 'value', label: '总数', align: 'right' }])
      expect(model.rows).toEqual([{ value: 10 }])
    })

    it('converts grouped result with custom labels', () => {
      const result: AggregateResult = {
        type: 'grouped',
        data: [
          { key: 'V1.0', value: 5, metrics: { records: 5, bugs: 7 } },
          { key: 'V2.0', value: 3, metrics: { records: 3, bugs: 4 } },
        ],
      }
      const model = toTableModel(result, {
        groupLabel: '版本',
        metricLabels: { records: '需求数', bugs: '问题数' },
      })
      expect(model.columns).toEqual([
        { key: 'group', label: '版本' },
        { key: 'records', label: '需求数', align: 'right' },
        { key: 'bugs', label: '问题数', align: 'right' },
      ])
      expect(model.rows).toEqual([
        { group: 'V1.0', records: 5, bugs: 7 },
        { group: 'V2.0', records: 3, bugs: 4 },
      ])
    })

    it('converts grouped result without metrics', () => {
      const result: AggregateResult = {
        type: 'grouped',
        data: [
          { key: 'fatal', value: 3 },
          { key: 'major', value: 5 },
        ],
      }
      const model = toTableModel(result, { groupLabel: '严重程度', valueLabel: '数量' })
      expect(model.columns).toEqual([
        { key: 'group', label: '严重程度' },
        { key: 'value', label: '数量', align: 'right' },
      ])
      expect(model.rows).toEqual([
        { group: 'fatal', value: 3 },
        { group: 'major', value: 5 },
      ])
    })

    it('provides default metric key and pie data fallback', () => {
      const result: AggregateResult = {
        type: 'grouped',
        data: [
          { key: 'A', value: 10, metrics: { total: 10 } },
        ],
      }

      expect(getDefaultMetricKey(result)).toBe('total')
      expect(toPieChartData(result, 'total')).toEqual([{ name: 'A', value: 10 }])
    })
  })
})
