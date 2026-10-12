/**
 * 轮内时间轴纯函数测试（方案：每轮「推理↔工具」交替时间轴 + task 委托
 * 嵌套子代理分解）。不触 ECharts 渲染，只断言段构造数学。
 */
import { describe, it, expect } from 'vitest'
import { buildTurnTimeline, buildTurnTimelineOption, segColor } from '../turnTimeline'
import type { PerfToolCall } from '@/api/aiSkills'

const T0 = Date.parse('2026-10-10T10:00:00')

const call = (over: Partial<PerfToolCall>): PerfToolCall => ({
  partId: 'p', tool: 'bash', args: '{}', state: 'completed',
  startedAt: null, durationMs: null, subtaskId: null,
  turnIndex: 0, isDelegation: false, ...over })

const turn = { createdAt: new Date(T0).toISOString(), durationMs: 60_000 }

describe('buildTurnTimeline', () => {
  it('交替重建：推理段填充工具间隙，和=轮时长', () => {
    // 轮 [0,60]s：bash [5,10]s、read [20,25]s → 推理 [0,5]+[10,20]+[25,60]
    const calls = [
      call({ partId: 't1', tool: 'bash', startedAt: new Date(T0 + 5_000).toISOString(),
             durationMs: 5_000 }),
      call({ partId: 't2', tool: 'read', startedAt: new Date(T0 + 20_000).toISOString(),
             durationMs: 5_000 }),
    ]
    const tl = buildTurnTimeline(turn, calls)
    expect(tl.segments.map(s => s.type))
      .toEqual(['inference', 'tool', 'inference', 'tool', 'inference'])
    expect(tl.segments[0]).toMatchObject({ start: T0, end: T0 + 5_000 })
    expect(tl.segments[1]).toMatchObject({ type: 'tool', start: T0 + 5_000 })
    expect(tl.segments[2]).toMatchObject({ type: 'inference',
                                           start: T0 + 10_000, end: T0 + 20_000 })
    const sum = tl.segments.reduce((a, s) => a + (s.end - s.start), 0)
    expect(sum).toBe(60_000)                       // 和 = 轮时长
    // 推理段 afterTool 指向前一工具（模型处理其结果的时间）
    expect(tl.segments[2].afterTool).toBe('bash 5.0s')
  })

  it('task 委托段嵌套子代理自己的推理/工具', () => {
    // 轮 [0,60]s：task [10,40]s；子代理内部 grep [15,20]s、bash [25,30]s
    const calls = [
      call({ partId: 'task1', tool: 'task',
             startedAt: new Date(T0 + 10_000).toISOString(), durationMs: 30_000,
             subtaskId: 'ses_sub', isDelegation: true }),
      call({ partId: 's1', tool: 'grep',
             startedAt: new Date(T0 + 15_000).toISOString(), durationMs: 5_000,
             subtaskId: 'ses_sub' }),
      call({ partId: 's2', tool: 'bash',
             startedAt: new Date(T0 + 25_000).toISOString(), durationMs: 5_000,
             subtaskId: 'ses_sub' }),
    ]
    const tl = buildTurnTimeline(turn, calls)
    const taskSeg = tl.segments.find(s => s.type === 'task')!
    expect(taskSeg).toMatchObject({ start: T0 + 10_000, end: T0 + 40_000 })
    const nested = tl.nested['task1']
    // 子代理内部：推理 [0,5]s → grep [5,10]s → 推理 [10,15]s → bash [15,20]s
    //              → 推理 [20,30]s（相对 task 起点 10s）
    expect(nested.map(s => s.type))
      .toEqual(['inference', 'tool', 'inference', 'tool', 'inference'])
    expect(nested[1]).toMatchObject({ type: 'tool', start: T0 + 15_000 })
    expect(nested[4]).toMatchObject({ start: T0 + 30_000, end: T0 + 40_000 })
  })

  it('subTurns 可得：子代理内部按轮次切推理↔工具，和=委托段时长', () => {
    // 轮 [0,60]s：task [10,40]s；子代理两轮 [15,25]s 与 [30,36]s；
    // 子代理内 grep [18,20]s（轮1 内）、bash [31,33]s（轮2 内）
    const calls = [
      call({ partId: 'task1', tool: 'task',
             startedAt: new Date(T0 + 10_000).toISOString(), durationMs: 30_000,
             subtaskId: 'ses_sub', isDelegation: true }),
      call({ partId: 's1', tool: 'grep',
             startedAt: new Date(T0 + 18_000).toISOString(), durationMs: 2_000,
             subtaskId: 'ses_sub' }),
      call({ partId: 's2', tool: 'bash',
             startedAt: new Date(T0 + 31_000).toISOString(), durationMs: 2_000,
             subtaskId: 'ses_sub' }),
    ]
    const subTurns = {
      ses_sub: [
        { createdAt: new Date(T0 + 15_000).toISOString(), durationMs: 10_000 },
        { createdAt: new Date(T0 + 30_000).toISOString(), durationMs: 6_000 },
      ],
    }
    const tl = buildTurnTimeline(turn, calls, subTurns)
    const nested = tl.nested['task1']!
    // 相邻推理段合并后 5 段：[10,18]（轮隙+轮1 首）→ grep[18,20] →
    // [20,31]（轮1 尾+轮隙+轮2 首）→ bash[31,33] → [33,40]（轮2 尾+尾隙）
    expect(nested[0]).toMatchObject({ type: 'inference', start: T0 + 10_000,
                                      end: T0 + 18_000 })
    expect(nested.filter(s => s.type === 'tool')
      .map(s => [s.start - T0, s.end - T0]))
      .toEqual([[18_000, 20_000], [31_000, 33_000]])
    const inf = nested.filter(s => s.type === 'inference')
    const sum = nested.reduce((a, s) => a + (s.end - s.start), 0)
    expect(sum).toBe(30_000)                     // 和 = 委托段时长
    // 全部推理段 label 一致（子代理推理），与主轴「模型推理」区分；相邻推理
    // 段已合并：[10,18]（轮隙+轮1 首）→ grep → [20,31]（轮1 尾+轮隙+轮2 首）
    // → bash → [33,40]（轮2 尾+尾隙）——和恒等于委托段时长
    expect(nested.map(s => s.type)).toEqual([
      'inference', 'tool', 'inference', 'tool', 'inference'])
    expect(inf.map(s => [s.start - T0, s.end - T0])).toEqual([
      [10_000, 18_000], [20_000, 31_000], [33_000, 40_000]])
    expect(inf.every(s => s.label === '子代理推理')).toBe(true)
  })

  it('subTurns 为空 dict（旧数据）：嵌套退化为纯间隙算法，行为不变', () => {
    const calls = [
      call({ partId: 'task1', tool: 'task',
             startedAt: new Date(T0 + 10_000).toISOString(), durationMs: 30_000,
             subtaskId: 'ses_sub', isDelegation: true }),
      call({ partId: 's1', tool: 'grep',
             startedAt: new Date(T0 + 15_000).toISOString(), durationMs: 5_000,
             subtaskId: 'ses_sub' }),
    ]
    const tl = buildTurnTimeline(turn, calls, {})
    const nested = tl.nested['task1']!
    expect(nested.map(s => s.type))
      .toEqual(['inference', 'tool', 'inference'])   // 与不传 subTurns 一致
  })

  it('多轮：每轮只铺本轮流次的调用（此前全量铺进每轮 → 每轮展开都一样）', () => {
    // 轮0 [0,30]s：bash [5,10]s；轮1 [30,60]s：read [40,45]s。
    // 轮0 的轴上不得出现 read；轮1 的轴上不得出现 bash。
    const calls = [
      call({ partId: 'b0', tool: 'bash', args: 'command=clone',
             startedAt: new Date(T0 + 5_000).toISOString(), durationMs: 5_000,
             turnIndex: 0 }),
      call({ partId: 'r1', tool: 'read', args: 'filePath=a.md',
             startedAt: new Date(T0 + 40_000).toISOString(), durationMs: 5_000,
             turnIndex: 1 }),
    ]
    const turn0 = { createdAt: new Date(T0).toISOString(), durationMs: 30_000 }
    const turn1 = { createdAt: new Date(T0 + 30_000).toISOString(),
                    durationMs: 30_000 }
    const tl0 = buildTurnTimeline(turn0, calls, {}, [], 0)
    const tl1 = buildTurnTimeline(turn1, calls, {}, [], 1)
    const labels0 = tl0.segments.filter(s => s.type === 'tool').map(s => s.ref)
    const labels1 = tl1.segments.filter(s => s.type === 'tool').map(s => s.ref)
    expect(labels0).toEqual(['b0'])
    expect(labels1).toEqual(['r1'])
    // 和恒等于各自轮时长
    expect(tl0.segments.reduce((a, s) => a + (s.end - s.start), 0)).toBe(30_000)
    expect(tl1.segments.reduce((a, s) => a + (s.end - s.start), 0)).toBe(30_000)
  })

  it('无时间戳调用进 unplaced 不上轴（旧数据）', () => {
    const calls = [call({ partId: 'old', tool: 'read', startedAt: null })]
    const tl = buildTurnTimeline(turn, calls)
    expect(tl.segments).toEqual([{ type: 'inference', start: T0, end: T0 + 60_000,
                                   label: '模型推理', afterTool: null }])
    expect(tl.unplaced).toHaveLength(1)
  })

  it('轮毛时长缺失 → 空段回退', () => {
    const tl = buildTurnTimeline({ createdAt: null, durationMs: 60_000 }, [])
    expect(tl.segments).toEqual([])
  })
})

describe('buildTurnTimelineOption', () => {
  it('主轴行+子代理嵌套行，段着色按类型', () => {
    const calls = [
      call({ partId: 'task1', tool: 'task',
             startedAt: new Date(T0 + 10_000).toISOString(), durationMs: 30_000,
             subtaskId: 'ses_sub', isDelegation: true }),
      call({ partId: 's1', tool: 'grep',
             startedAt: new Date(T0 + 15_000).toISOString(), durationMs: 5_000,
             subtaskId: 'ses_sub' }),
    ]
    const tl = buildTurnTimeline(turn, calls)
    const opt = buildTurnTimelineOption(tl)
    const data = (opt.series as any[])[0].data
    expect(data.filter((d: any) => d.row === 1).length).toBeGreaterThanOrEqual(2)
    expect(data.filter((d: any) => d.row === 0).length).toBeGreaterThanOrEqual(1)
    expect((opt.yAxis as any).data).toEqual(['子代理', '轮次'])
  })

  it('segColor 按类型着色（推理黄/工具绿/委托紫）', () => {
    expect(segColor('inference')).toBe('#f6c344')
    expect(segColor('tool')).toBe('#67c23a')
    expect(segColor('task')).toBe('#9254de')
  })
})
