import type { PerfTaskEntry, PerfAttemptDetail } from '@/api/aiSkills'

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

/** 任务下钻时间轴瀑布：y 轴为段类型（子代理/模型）、x 轴为绝对时间。
 *  每段 data 项挂 __ref（messageId/subtaskId），诊断「定位」可据此跳转。 */
export function buildWaterfallOption(detail: PerfAttemptDetail) {
  const segs: { name: string; type: string; ref: string;
                start: number; end: number }[] = []
  for (const turn of detail.turns) {
    if (turn.durationMs == null || !turn.createdAt) continue
    const s = Date.parse(turn.createdAt)
    segs.push({ name: `模型轮次 ${(turn.durationMs / 1000).toFixed(1)}s`,
                type: 'model', ref: turn.messageId, start: s, end: s + turn.durationMs })
  }
  for (const st of detail.subtasks) {
    if (!st.startedAt) continue
    const s = Date.parse(st.startedAt)
    const e = st.finishedAt ? Date.parse(st.finishedAt) : s + st.wallMs
    segs.push({ name: `子代理 ${st.agent || ''} ${(st.wallMs / 1000).toFixed(1)}s`,
                type: 'subagent', ref: st.subtaskId, start: s, end: e })
  }
  return {
    tooltip: { formatter: (p: any) => p.name },
    grid: { left: 90, right: 30, top: 20, bottom: 40 },
    xAxis: { type: 'time' },
    yAxis: { type: 'category', data: ['子代理', '模型'], inverse: false },
    series: [{
      type: 'custom',
      renderItem: (_params: any, api: any) => {
        const catIdx = api.value(0)
        const left = api.coord([api.value(1), catIdx])[0]
        const right = api.coord([api.value(2), catIdx])[0]
        const top = api.coord([api.value(1), catIdx])[1]
        return {
          type: 'rect',
          shape: { x: left, y: top - 12, width: Math.max(1, right - left), height: 24 },
          style: { fill: catIdx === 1 ? '#409eff' : '#e6a23c' },
        }
      },
      encode: { x: [1, 2], y: 0 },
      data: segs.map(s => ({
        name: s.name, value: [s.type === 'model' ? 1 : 0, s.start, s.end],
        __ref: s.ref,
      })),
    }],
  }
}
