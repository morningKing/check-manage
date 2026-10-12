import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/api/aiChat', () => ({
  getSubtaskMessages: vi.fn(),
}))

import { getSubtaskMessages } from '@/api/aiChat'
import SubtaskBubble from '@/components/ai-chat/SubtaskBubble.vue'

describe('SubtaskBubble', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('未展开时不发请求', () => {
    mount(SubtaskBubble, {
      props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
               description: 'do x', status: 'running', depth: 1, fetchFn: getSubtaskMessages },
    })
    expect(getSubtaskMessages).not.toHaveBeenCalled()
  })

  it('点击展开后发起一次请求', async () => {
    vi.mocked(getSubtaskMessages).mockResolvedValue({
      subtask: { id: 'ses_x', agent: 'build', description: 'do x', status: 'completed', error: null },
      messages: [], truncated: false, total: 0,
    })
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
               description: 'do x', status: 'completed', depth: 1, fetchFn: getSubtaskMessages },
    })
    await wrapper.find('.subtask-bubble__head').trigger('click')
    await wrapper.vm.$nextTick()
    expect(getSubtaskMessages).toHaveBeenCalledTimes(1)
  })

  it('再次展开会重新拉取（子代理可能已有新轨迹）', async () => {
    vi.mocked(getSubtaskMessages).mockResolvedValue({
      subtask: { id: 'ses_x', agent: 'build', description: 'do x', status: 'completed', error: null },
      messages: [], truncated: false, total: 0,
    })
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
               description: 'do x', status: 'completed', depth: 1, fetchFn: getSubtaskMessages },
    })
    const head = wrapper.find('.subtask-bubble__head')
    await head.trigger('click')
    await wrapper.vm.$nextTick()
    await head.trigger('click')   // 收起
    await head.trigger('click')   // 再展开 → 刷新
    await wrapper.vm.$nextTick()
    expect(getSubtaskMessages).toHaveBeenCalledTimes(2)
  })

  it('running 态轮询遇拉取失败不死亡：失败后仍按节奏续排，恢复后内容继续刷新', async () => {
    // 生产缺陷（2026-10-12 用户报告「区块内容卡住不刷新，直到任务结束才完整」）：
    // schedulePoll 的续排条件是 result.value && result.value.subtask.status==='running'——
    // 首次拉取失败（子会话消息端点瞬时 404/网络抖动）时 result 仍是 null，
    // falsy 短路走 stopPolling()，轮询永久死亡；running 态下 emptyRetry 又被
    // 显式跳过（scheduleEmptyRetry 里 status==='running' return）→ 区块冻结
    // 到父回合结束（_reloadPersisted 整替）才有内容。
    vi.useFakeTimers()
    try {
      const ok = (status: 'running' | 'completed') => ({
        subtask: { id: 'ses_x', agent: 'build', description: 'do x', status, error: null },
        messages: [], truncated: false, total: 0,
      })
      let calls = 0
      vi.mocked(getSubtaskMessages).mockImplementation(async () => {
        calls += 1
        if (calls <= 2) throw new Error('transient 404')   // 首拉+首个轮询回调连续失败
        return ok('running')
      })
      const wrapper = mount(SubtaskBubble, {
        props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
                 description: 'do x', status: 'running', depth: 1, fetchFn: getSubtaskMessages },
        attachTo: document.body,
      })
      await wrapper.find('.subtask-bubble__head').trigger('click')
      await vi.advanceTimersByTimeAsync(0)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(1)   // 首拉失败（被吞）

      // 轮询必须仍然续排：2.5s 后再拉（此刻成功）
      await vi.advanceTimersByTimeAsync(2_500)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(2)
      // 恢复后继续正常节奏
      await vi.advanceTimersByTimeAsync(2_500)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(3)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('展开态 running 时轮询刷新；到终态拉最终数据，空结果限次重试等落库', async () => {
    vi.useFakeTimers()
    try {
      const mk = (status: 'running' | 'completed' | 'failed') => ({
        subtask: { id: 'ses_x', agent: 'build', description: 'do x', status, error: null },
        messages: [], truncated: false, total: 0,
      })
      vi.mocked(getSubtaskMessages).mockImplementation(async () => mk('running'))
      const wrapper = mount(SubtaskBubble, {
        props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
                 description: 'do x', status: 'running', depth: 1, fetchFn: getSubtaskMessages },
        attachTo: document.body,
      })
      await wrapper.find('.subtask-bubble__head').trigger('click')
      await vi.advanceTimersByTimeAsync(0)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(1)

      await vi.advanceTimersByTimeAsync(2500)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(2500)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(3)

      // 状态翻转 completed：立即终态刷新，不再按 running 节奏轮询
      vi.mocked(getSubtaskMessages).mockImplementation(async () => mk('completed'))
      await wrapper.setProps({ status: 'completed' })
      await vi.advanceTimersByTimeAsync(0)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(4)

      // 但空结果会触发限次静默重试（每 5s 一次）：终态先于服务端落库到达时，
      // 气泡不该永远停在「还没有对话记录」
      await vi.advanceTimersByTimeAsync(5_000)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(5)
      await vi.advanceTimersByTimeAsync(5_000)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(6)

      // 重试有上限（EMPTY_RETRY_MAX=24）：推进 2 分钟后不再增长
      await vi.advanceTimersByTimeAsync(120_000)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(4 + 24)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('REST 返回的子任务已终态时轮询自动停止（父级状态滞后场景）', async () => {
    vi.useFakeTimers()
    try {
      // 父级 part 仍标 running，但服务端子任务已完成
      vi.mocked(getSubtaskMessages).mockResolvedValue({
        subtask: { id: 'ses_x', agent: 'build', description: 'do x', status: 'completed', error: null },
        messages: [], truncated: false, total: 0,
      })
      const wrapper = mount(SubtaskBubble, {
        props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
                 description: 'do x', status: 'running', depth: 1, fetchFn: getSubtaskMessages },
        attachTo: document.body,
      })
      await wrapper.find('.subtask-bubble__head').trigger('click')
      await vi.advanceTimersByTimeAsync(0)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(2500)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(10_000)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(2)   // 已终态：停止
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('超过深度上限不渲染可展开内容', () => {
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
               description: 'do x', status: 'running', depth: 6, fetchFn: getSubtaskMessages },
    })
    expect(wrapper.text()).toContain('已达展示深度上限')
    expect(wrapper.find('.subtask-bubble__head').exists()).toBe(false)
  })

  it('展开后渲染完整执行轨迹：委托输入 + 思考 + 工具调用 + 输出', async () => {
    vi.mocked(getSubtaskMessages).mockResolvedValue({
      subtask: { id: 'ses_x', agent: 'build', description: 'do x', status: 'completed', error: null },
      messages: [
        { id: 'u1', role: 'user', content: [{ type: 'text', text: 'delegation input' }] },
        { id: 'a1', role: 'assistant', content: [
          { type: 'reasoning', text: 'thinking hard' },
          { type: 'tool_use', name: 'bash', title: 'run ls', status: 'completed',
            input: { command: 'ls' }, result: 'file.txt', durationMs: 10 },
          { type: 'text', text: 'final answer' },
        ] },
      ],
      truncated: false, total: 2,
    })
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
               description: 'do x', status: 'completed', depth: 1, fetchFn: getSubtaskMessages },
    })
    await wrapper.find('.subtask-bubble__head').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('委托输入')
    expect(wrapper.text()).toContain('delegation input')
    expect(wrapper.find('.stub-thinking').exists()).toBe(true)
    expect(wrapper.text()).toContain('thinking hard')
    expect(wrapper.text()).toContain('final answer')
    expect(wrapper.findComponent({ name: 'ToolCallBubble' }).exists()).toBe(true)
  })
})
