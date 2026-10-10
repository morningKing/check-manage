/**
 * 轮次内时间轴（方案：每轮一条「推理 ↔ 工具」交替的时间轴瀑布）。
 *
 * 从账本逐调用（started_at/duration_ms）重建轮内时序：轮毛时长被切成
 * 互斥的推理段（工具之间的间隔=模型思考）与工具段，和恒等于轮时长；
 * task 调用是「开子代理」的委托段，其内部（子代理自己的推理与工具）
 * 以同样算法递归重建，时间对齐在同一轴上——模型针对哪些工具前后想了
 * 多久一目了然。
 */
import type { PerfToolCall } from '@/api/aiSkills'

export interface TurnSeg {
  type: 'inference' | 'tool' | 'task'
  start: number
  end: number
  /** tool/task 段=调用描述；inference 段=时长文本 */
  label: string
  /** tool/task 段的调用引用（partId） */
  ref?: string
  /** 推理段：该推理发生在哪个工具之后（null=轮首初始推理） */
  afterTool?: string | null
}

export interface TurnTimeline {
  /** 主轴段（推理/工具/委托交替，时间序，互斥且和=轮时长） */
  segments: TurnSeg[]
  /** task 段 → 子代理内部的推理/工具段（时间对齐主轴） */
  nested: Record<string, TurnSeg[]>
  /** 无法落到时间轴上的调用（缺 started_at/duration_ms——旧数据） */
  unplaced: PerfToolCall[]
}

function parseMs(v: string | null): number | null {
  if (!v) return null
  const t = Date.parse(v)
  return Number.isFinite(t) ? t : null
}

/** 轮时窗内的交替段重建：光标扫过排序后的调用，间隙即推理。 */
function spanSegments(windowStart: number, windowEnd: number,
                      calls: PerfToolCall[]): { segments: TurnSeg[]; unplaced: PerfToolCall[] } {
  const segments: TurnSeg[] = []
  const unplaced: PerfToolCall[] = []
  let cursor = windowStart
  for (const c of [...calls].sort((a, b) =>
    (parseMs(a.startedAt) ?? 0) - (parseMs(b.startedAt) ?? 0))) {
    const s = parseMs(c.startedAt)
    const isTask = c.tool === 'task'
    const end = s != null && c.durationMs != null ? s + c.durationMs : null
    if (s == null || end == null || end <= s) {
      unplaced.push(c)           // 无时间或零长（旧数据）→ 不上轴
      continue
    }
    const clampedStart = Math.max(s, cursor)
    if (clampedStart > cursor) {
      segments.push({ type: 'inference', start: cursor, end: clampedStart,
                      label: '模型推理',
                      afterTool: segments[segments.length - 1]?.type === 'tool'
                        ? segments[segments.length - 1].label : null })
    }
    const label = isTask ? `开子代理（等待 ${(c.durationMs! / 1000).toFixed(1)}s）`
      : `${c.tool} ${(c.durationMs! / 1000).toFixed(1)}s`
    segments.push({ type: isTask ? 'task' : 'tool', start: s, end: Math.max(end, clampedStart),
                    label, ref: c.partId })
    cursor = Math.max(cursor, end)
  }
  if (cursor < windowEnd) {
    segments.push({ type: 'inference', start: cursor, end: windowEnd,
                    label: '模型推理', afterTool: null })
  }
  return { segments, unplaced }
}

/** 轮内时间轴：主轴（推理/工具/委托交替）+ 每个 task 委托段内部（子代理
 *  自己的推理/工具）的嵌套段。轮毛时长缺失或调用缺时间无法重建时返回
 *  segments=[]（前端回退到明细列表）。 */
export function buildTurnTimeline(
  turn: { createdAt: string | null; durationMs: number },
  calls: PerfToolCall[]): TurnTimeline {
  const w0 = parseMs(turn.createdAt)
  if (!w0 || turn.durationMs == null) return { segments: [], nested: {}, unplaced: calls }
  const w1 = w0 + turn.durationMs
  // 根轴：根会话调用 + task 委托调用（task 带 subtaskId 但它是父轴上的
  // 委托事件）；子代理内部的调用走嵌套分解
  const root = calls.filter(c => !c.subtaskId || c.tool === 'task')
  const { segments, unplaced } = spanSegments(w0, w1, root)
  const nested: Record<string, TurnSeg[]> = {}
  for (const seg of segments) {
    if (seg.type !== 'task' || !seg.ref) continue
    const call = calls.find(c => c.partId === seg.ref)
    if (!call?.subtaskId) continue
    // 委托段内部 = 子代理自己的工具与推理间隙（排除 task 调用行本身——
    // 它就是这段的边界，否则自包含成环）
    const subCalls = calls.filter(c => c.subtaskId === call.subtaskId
                                  && c.partId !== seg.ref)
    nested[seg.ref] = spanSegments(seg.start, seg.end, subCalls).segments
  }
  return { segments, nested, unplaced }
}

const SEG_COLORS: Record<TurnSeg['type'], string> = {
  inference: '#f6c344',   // 模型推理（思考/输出）
  tool: '#67c23a',        // 工具执行
  task: '#9254de',        // 开子代理（委托等待）
}

export function segColor(type: TurnSeg['type']): string {
  return SEG_COLORS[type]
}

/** 轮内时间轴 ECharts option：两行（上=子代理嵌套分解、下=轮次主轴），
 *  x 为绝对时间，custom rect 段按类型着色。 */
export function buildTurnTimelineOption(timeline: TurnTimeline) {
  interface Item { name: string; value: [number, number, number]; row: number;
                   type: TurnSeg['type']; ref?: string }
  const data: Item[] = []
  for (const s of timeline.segments) {
    data.push({ name: s.label, row: 1, type: s.type, ref: s.ref,
                value: [1, s.start, s.end] })
  }
  for (const seg of timeline.segments) {
    if (seg.type !== 'task' || !seg.ref) continue
    for (const ns of timeline.nested[seg.ref] || []) {
      data.push({ name: ns.label, row: 0, type: ns.type,
                  value: [0, ns.start, ns.end] })
    }
  }
  return {
    tooltip: { formatter: (p: any) => p.name },
    grid: { left: 70, right: 20, top: 12, bottom: 30 },
    xAxis: { type: 'time' },
    yAxis: { type: 'category', data: ['子代理', '轮次'] },
    series: [{
      type: 'custom',
      renderItem: (params: any, api: any) => {
        const item = data[params.dataIndex]
        const catIdx = api.value(0)
        const left = api.coord([api.value(1), catIdx])[0]
        const right = api.coord([api.value(2), catIdx])[0]
        const top = api.coord([api.value(1), catIdx])[1]
        return {
          type: 'rect',
          shape: { x: left, y: top - 12,
                   width: Math.max(1, right - left), height: 24 },
          style: { fill: SEG_COLORS[item.type] },
        }
      },
      encode: { x: [1, 2], y: 0 },
      data,
    }],
  }
}
