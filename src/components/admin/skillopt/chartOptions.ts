import type { PerfTaskEntry } from '@/api/aiSkills'

export function buildTrendOption(tasks: PerfTaskEntry[]) {
  const sorted = [...tasks].sort((a, b) =>
    (a.startedAt || '').localeCompare(b.startedAt || ''))
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['墙钟', '模型占比'] },
    grid: { left: 60, right: 50, top: 40, bottom: 60 },
    xAxis: { type: 'category', data: sorted.map(t => t.startedAt || t.attemptId) },
    yAxis: [
      { type: 'value', name: '墙钟(ms)' },
      { type: 'value', name: '模型占比', max: 1 },
    ],
    series: [
      // __sources 供 PerfTrendChart 组件层按 sourceType 上色（bar series 的
      // color 回调经 dataIndex 查它），ECharts 忽略未知键、纯函数可断言
      { name: '墙钟', type: 'bar', data: sorted.map(t => t.wallMs),
        __sources: sorted.map(t => t.sourceType) },
      { name: '模型占比', type: 'line', yAxisIndex: 1,
        data: sorted.map(t => t.modelRatio), smooth: true },
    ],
  }
}
