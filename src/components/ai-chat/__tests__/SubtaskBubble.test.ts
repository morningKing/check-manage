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

  it('再次展开不重复发请求', async () => {
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
    await head.trigger('click')   // 再展开
    await wrapper.vm.$nextTick()
    expect(getSubtaskMessages).toHaveBeenCalledTimes(1)
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
