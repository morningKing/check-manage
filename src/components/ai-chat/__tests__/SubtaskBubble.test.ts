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

  it('展开态 running 时轮询刷新；到终态拉一次最终数据后停止', async () => {
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

      // 状态翻转 completed：立即终态刷新，之后不再轮询
      vi.mocked(getSubtaskMessages).mockImplementation(async () => mk('completed'))
      await wrapper.setProps({ status: 'completed' })
      await vi.advanceTimersByTimeAsync(0)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(4)
      await vi.advanceTimersByTimeAsync(10_000)
      expect(getSubtaskMessages).toHaveBeenCalledTimes(4)
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
