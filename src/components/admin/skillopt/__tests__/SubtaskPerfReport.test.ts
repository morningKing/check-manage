/**
 * SubtaskPerfReport 组件测试（2026-10-15 子任务性能报告）：
 * 六大分区渲染 / Top 排序 / 失败过滤 / 重复清单 / 子代理分解 /
 * 复制 Markdown 文本内容 / 空态。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const mockPerfAttempt = vi.fn()
const mockChildPerf = vi.fn()
vi.mock('@/api/aiSkills', () => ({
  perfAttempt: (...a: unknown[]) => mockPerfAttempt(...a),
}))
vi.mock('@/api/aiBatchAdmin', () => ({
  getAdminChildPerf: (...a: unknown[]) => mockChildPerf(...a),
}))

import SubtaskPerfReport from '../SubtaskPerfReport.vue'

const DETAIL = {
  attempt: {
    attemptId: 'aid1', sessionId: 'sid1', sourceType: 'batch',
    status: 'completed', startedAt: '2026-10-15T10:00:00',
    finishedAt: '2026-10-15T10:05:00', wallMs: 300_000, modelMs: 200_000,
    modelRatio: 0.67, subagentWaitMs: 60_000, idleMs: 10_000, turns: 2,
    tokensIn: 1000, tokensOut: 500, subtaskCount: 1,
    completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
  },
  coverage: { wallMs: 300_000, modelMs: 200_000, subagentWaitMs: 60_000,
              idleMs: 10_000, toolMs: 30_000 },
  turns: [
    { messageId: 'm1', createdAt: '2026-10-15T10:00:10', durationMs: 5000,
      tokensIn: 100, tokensOut: 50, preview: '快轮', toolCount: 1 },
    { messageId: 'm2', createdAt: '2026-10-15T10:01:00', durationMs: 60_000,
      tokensIn: 200, tokensOut: 100, preview: '慢轮', toolCount: 2 },
  ],
  subtasks: [
    { subtaskId: 'st1', agent: 'general', description: '写报告',
      status: 'completed', startedAt: null, finishedAt: null, wallMs: 60_000 },
  ],
  subTurns: { st1: [{ createdAt: '2026-10-15T10:02:00', durationMs: 20_000 }] },
  tools: {
    errorCount: 1,
    repeats: [{ tool: 'read', argsPreview: 'filePath=a.txt', count: 3,
                totalMs: 900 }],
    byTool: [],
    durationAvailable: true,
    callsTruncated: false,
    calls: [
      { partId: 'p1', tool: 'bash', args: 'command=slow', state: 'completed',
        startedAt: '2026-10-15T10:00:20', durationMs: 20_000,
        subtaskId: null, turnIndex: 0, isDelegation: false },
      { partId: 'p2', tool: 'read', args: 'filePath=a.txt', state: 'error',
        startedAt: '2026-10-15T10:00:30', durationMs: 300,
        subtaskId: 'st1', turnIndex: null, isDelegation: false },
      { partId: 'p3', tool: 'read', args: 'filePath=a.txt', state: 'completed',
        startedAt: '2026-10-15T10:00:40', durationMs: 300,
        subtaskId: 'st1', turnIndex: null, isDelegation: false },
      { partId: 'p4', tool: 'read', args: 'filePath=a.txt', state: 'completed',
        startedAt: '2026-10-15T10:00:50', durationMs: 300,
        subtaskId: 'st1', turnIndex: null, isDelegation: false },
      { partId: 'p5', tool: 'task', args: 'description=委派', state: 'completed',
        startedAt: '2026-10-15T10:01:00', durationMs: 60_000,
        subtaskId: null, turnIndex: 0, isDelegation: true },
    ],
  },
  skills: [],
  completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
}

const writeText = vi.fn().mockResolvedValue(undefined)

async function mountReport(props: Record<string, unknown>) {
  const wrapper = mount(SubtaskPerfReport, { props })
  await flushPromises()
  return wrapper
}

describe('SubtaskPerfReport', () => {
  beforeEach(() => {
    mockPerfAttempt.mockReset()
    mockChildPerf.mockReset()
    writeText.mockClear()
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText }, configurable: true,
    })
  })

  it('attemptId 模式：分区渲染 + Top 排序 + 失败过滤 + 重复清单', async () => {
    mockPerfAttempt.mockResolvedValue(DETAIL)
    const w = await mountReport({ attemptId: 'aid1' })
    expect(mockPerfAttempt).toHaveBeenCalledWith('aid1')

    // 概要卡：墙钟/失败/重复计数
    expect(w.text()).toContain('5m0s')                 // 墙钟 300s
    expect(w.find('[data-test="top-calls"]').exists()).toBe(true)

    // 最慢调用 Top：首行是 60s 的开子代理委派（最长）
    const topRows = w.findAll('[data-test="top-calls"] .el-table__body tr')
    expect(topRows.length).toBeGreaterThan(0)
    expect(topRows[0].text()).toContain('开子代理')
    expect(topRows[0].text()).toContain('1m0s')
    // 第二行是 20s 的 bash
    expect(topRows[1].text()).toContain('bash')

    // 失败调用：仅 1 条（read，归属子代理 general）
    const failRows = w.findAll('[data-test="failed-calls"] .el-table__body tr')
    expect(failRows).toHaveLength(1)
    expect(failRows[0].text()).toContain('read')
    expect(failRows[0].text()).toContain('general')

    // 重复调用：read ×3
    const repRows = w.findAll('[data-test="repeat-calls"] .el-table__body tr')
    expect(repRows).toHaveLength(1)
    expect(repRows[0].text()).toContain('read')
    expect(repRows[0].text()).toContain('×3')

    // 推理 Top：慢轮在前
    const turnRows = w.findAll('[data-test="top-turns"] .el-table__body tr')
    expect(turnRows[0].text()).toContain('慢轮')

    // 子代理分解：general + 统计
    const groups = w.find('[data-test="subagent-groups"]')
    expect(groups.exists()).toBe(true)
    expect(groups.text()).toContain('general')
    expect(groups.text()).toContain('工具 3')
    expect(groups.text()).toContain('失败 1')
    expect(groups.text()).toContain('重复 1')
  })

  it('复制 Markdown：含全部分区与关键数据', async () => {
    mockPerfAttempt.mockResolvedValue(DETAIL)
    const w = await mountReport({ attemptId: 'aid1' })
    await w.find('[data-test="copy-md"]').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledTimes(1)
    const md = writeText.mock.calls[0][0] as string
    expect(md).toContain('# 子任务性能报告')
    expect(md).toContain('## 概要')
    expect(md).toContain('## 最慢工具调用 Top 10')
    expect(md).toContain('| 1 | 开子代理 | 1m0s')
    expect(md).toContain('## 推理耗时 Top 10')
    expect(md).toContain('## 失败工具调用（1）')
    expect(md).toContain('| read | 300ms')
    expect(md).toContain('## 重复调用')
    expect(md).toContain('| read | filePath=a.txt | ×3')
    expect(md).toContain('## 子代理分解（1）')
    expect(md).toContain('### general')
  })

  it('batchId+sid 模式：调用子任务端点', async () => {
    mockChildPerf.mockResolvedValue(DETAIL)
    const w = await mountReport({ batchId: 'b1', sid: 'sid1' })
    expect(mockChildPerf).toHaveBeenCalledWith('b1', 'sid1')
    expect(w.find('[data-test="top-calls"]').exists()).toBe(true)
  })

  it('无执行记录：显示空态', async () => {
    mockChildPerf.mockResolvedValue({ attempt: null, empty: true })
    const w = await mountReport({ batchId: 'b1', sid: 'sid1' })
    expect(w.find('[data-test="perf-empty"]').exists()).toBe(true)
    expect(w.text()).toContain('还没有可分析的执行记录')
  })
})
