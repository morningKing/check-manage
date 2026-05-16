import type {
  AggregateGroupedRow,
  AggregateMatrixCell,
  AggregateResult,
} from '@/api/dashboard'

export interface ChartSeriesModel {
  name: string
  data: number[]
}

export interface CartesianChartModel {
  categories: Array<string | number>
  series: ChartSeriesModel[]
}

export interface TableColumnModel {
  key: string
  label: string
  align?: 'left' | 'right' | 'center'
}

export interface TableModel {
  columns: TableColumnModel[]
  rows: Array<Record<string, string | number>>
}

export interface RadarDataModel {
  dimensions: string[]
  values: number[]
}

interface TableLabelOptions {
  groupLabel?: string
  columnLabel?: string
  valueLabel?: string
  metricLabels?: Record<string, string>
}

function metricKeysFromRecord(record?: { metrics?: Record<string, number> } | null): string[] {
  return record?.metrics ? Object.keys(record.metrics) : []
}

export function getDefaultMetricKey(result?: AggregateResult | null): string | undefined {
  if (!result) return undefined
  if (result.type === 'single') return metricKeysFromRecord(result)[0]
  if (result.type === 'grouped') return metricKeysFromRecord(result.data[0])[0]
  return metricKeysFromRecord(result.data[0])[0]
}

export function getMetricValue(
  record: AggregateGroupedRow | AggregateMatrixCell | { value: number; metrics?: Record<string, number> },
  metricKey?: string,
): number {
  if (!record.metrics || !Object.keys(record.metrics).length) {
    return record.value ?? 0
  }
  if (metricKey && metricKey in record.metrics) {
    return record.metrics[metricKey] ?? 0
  }
  const firstMetricKey = Object.keys(record.metrics)[0]
  return firstMetricKey ? record.metrics[firstMetricKey] ?? 0 : record.value ?? 0
}

export function toCartesianChartModel(
  result?: AggregateResult | null,
  metricKey?: string,
): CartesianChartModel {
  if (!result || result.type === 'single') {
    return { categories: [], series: [] }
  }

  if (result.type === 'grouped') {
    const categories = result.data.map(item => item.key)
    const metricKeys = metricKeysFromRecord(result.data[0])

    if (metricKeys.length > 1 && !metricKey) {
      return {
        categories,
        series: metricKeys.map(name => ({
          name,
          data: result.data.map(item => getMetricValue(item, name)),
        })),
      }
    }

    return {
      categories,
      series: [{
        name: metricKey || metricKeys[0] || 'value',
        data: result.data.map(item => getMetricValue(item, metricKey)),
      }],
    }
  }

  const categories = result.rows
  const lookup = new Map(result.data.map(cell => [`${cell.rowKey}::${cell.columnKey}`, cell] as const))

  return {
    categories,
    series: result.columns.map(column => ({
      name: String(column),
      data: categories.map(row => {
        const cell = lookup.get(`${row}::${column}`)
        return cell ? getMetricValue(cell, metricKey) : 0
      }),
    })),
  }
}

export function toPieChartData(
  result?: AggregateResult | null,
  metricKey?: string,
): Array<{ name: string; value: number }> {
  if (!result) return []
  if (result.type === 'single') {
    return [{ name: metricKey || 'value', value: getMetricValue(result, metricKey) }]
  }
  if (result.type === 'grouped') {
    return result.data.map(item => ({
      name: String(item.key),
      value: getMetricValue(item, metricKey),
    }))
  }
  return result.data.map(item => ({
    name: `${item.rowKey} / ${item.columnKey}`,
    value: getMetricValue(item, metricKey),
  }))
}

export function toGaugeValue(result?: AggregateResult | null): number {
  if (!result || result.type !== 'single') return 0
  return result.value ?? 0
}

export function toRadarDataModel(result?: AggregateResult | null): RadarDataModel {
  if (!result || result.type !== 'single') {
    return { dimensions: [], values: [] }
  }
  const metrics = result.metrics || {}
  return {
    dimensions: Object.keys(metrics),
    values: Object.values(metrics),
  }
}

export function toFunnelData(
  result?: AggregateResult | null,
): Array<{ name: string; value: number }> {
  if (!result || result.type !== 'grouped') return []
  return result.data.map(item => ({
    name: String(item.key),
    value: item.value ?? 0,
  }))
}

export function getRingTotalValue(
  result?: AggregateResult | null,
  metricKey?: string,
): number {
  if (!result) return 0
  if (result.type === 'single') {
    return getMetricValue(result, metricKey)
  }
  if (result.type === 'grouped') {
    return result.data.reduce((sum, item) => sum + getMetricValue(item, metricKey), 0)
  }
  return result.data.reduce((sum, item) => sum + getMetricValue(item, metricKey), 0)
}

export function toTableModel(
  result?: AggregateResult | null,
  options: TableLabelOptions = {},
): TableModel {
  const groupLabel = options.groupLabel || '分组'
  const columnLabel = options.columnLabel || '系列'
  const valueLabel = options.valueLabel || '值'
  const metricLabels = options.metricLabels || {}

  if (!result) {
    return { columns: [], rows: [] }
  }

  if (result.type === 'single') {
    const metricKeys = metricKeysFromRecord(result)
    if (metricKeys.length) {
      return {
        columns: metricKeys.map(key => ({
          key,
          label: metricLabels[key] || key,
          align: 'right',
        })),
        rows: [Object.fromEntries(metricKeys.map(key => [key, result.metrics?.[key] ?? 0]))],
      }
    }

    return {
      columns: [{ key: 'value', label: valueLabel, align: 'right' }],
      rows: [{ value: result.value ?? 0 }],
    }
  }

  if (result.type === 'grouped') {
    const metricKeys = metricKeysFromRecord(result.data[0])
    if (metricKeys.length) {
      return {
        columns: [
          { key: 'group', label: groupLabel },
          ...metricKeys.map(key => ({
            key,
            label: metricLabels[key] || key,
            align: 'right' as const,
          })),
        ],
        rows: result.data.map(item => ({
          group: String(item.key),
          ...Object.fromEntries(metricKeys.map(key => [key, item.metrics?.[key] ?? 0])),
        })),
      }
    }

    return {
      columns: [
        { key: 'group', label: groupLabel },
        { key: 'value', label: valueLabel, align: 'right' },
      ],
      rows: result.data.map(item => ({ group: String(item.key), value: item.value ?? 0 })),
    }
  }

  const metricKeys = metricKeysFromRecord(result.data[0])
  if (metricKeys.length) {
    return {
      columns: [
        { key: 'rowKey', label: groupLabel },
        { key: 'columnKey', label: columnLabel },
        ...metricKeys.map(key => ({
          key,
          label: metricLabels[key] || key,
          align: 'right' as const,
        })),
      ],
      rows: result.data.map(item => ({
        rowKey: String(item.rowKey),
        columnKey: String(item.columnKey),
        ...Object.fromEntries(metricKeys.map(key => [key, item.metrics?.[key] ?? 0])),
      })),
    }
  }

  return {
    columns: [
      { key: 'rowKey', label: groupLabel },
      { key: 'columnKey', label: columnLabel },
      { key: 'value', label: valueLabel, align: 'right' },
    ],
    rows: result.data.map(item => ({
      rowKey: String(item.rowKey),
      columnKey: String(item.columnKey),
      value: item.value ?? 0,
    })),
  }
}
