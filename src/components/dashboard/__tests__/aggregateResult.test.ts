import { describe, expect, it } from 'vitest'
import type { AggregateResult } from '@/api/dashboard'
import {
  getDefaultMetricKey,
  toCartesianChartModel,
  toPieChartData,
  toTableModel,
} from '../aggregateResult'

describe('aggregateResult helpers', () => {
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
