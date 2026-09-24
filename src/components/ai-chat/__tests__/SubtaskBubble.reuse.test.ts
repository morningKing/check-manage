/**
 * 子代理会话复用（2026-09-24）前端回归：
 * - 气泡头显示子会话 task_id 标识（需求 3），点击复制；
 * - 复用会话（segments ≥2）显示「已复用·N 段」徽标；
 * - 展开体在任务段边界渲染分段头（需求 2）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/api/aiChat', () => ({
  getSubtaskMessages: vi.fn(),
  compactSubtask: vi.fn(),
}))

import { getSubtaskMessages, type SubtaskMessagesResult } from '@/api/aiChat'
import SubtaskBubble from '@/components/ai-chat/SubtaskBubble.vue'

describe('SubtaskBubble 会话复用标识', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  const mkSegments = () => ([
    { ord: 0, turn: 0, label: 'task one', firstMsgId: 'u1', startedAt: '2026-09-24T10:00:00Z' },
    { ord: 1, turn: 3, label: 'task two', firstMsgId: 'u2', startedAt: '2026-09-24T10:05:00Z' },
  ])

  function mkResult(): SubtaskMessagesResult {
    return {
      subtask: {
        id: 'ses_dev_a1', agent: 'dev', description: '修改任务',
        status: 'completed', error: null,
        segments: mkSegments(),
      },
      messages: [
        { id: 'u1', role: 'user', content: [{ type: 'text', text: 'task one' }] },
        { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'round one done' }] },
        { id: 'u2', role: 'user', content: [{ type: 'text', text: 'task two' }] },
        { id: 'a2', role: 'assistant', content: [{ type: 'text', text: 'round two done' }] },
      ],
      truncated: false,
      total: 4,
    }
  }

  it('气泡头显示 task_id 标识（截断展示，title 含完整 id）', async () => {
    vi.mocked(getSubtaskMessages).mockResolvedValue(mkResult())
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_dev_a1', sessionId: 's-1', agent: 'dev',
               description: '修改任务', status: 'completed', depth: 1,
               fetchFn: getSubtaskMessages },
    })
    const chip = wrapper.find('.subtask-bubble__taskid')
    expect(chip.exists()).toBe(true)
    expect(chip.attributes('title')).toContain('ses_dev_a1')
  })

  it('复用会话显示「已复用·N 段」徽标，展开渲染任务段边界与轮次', async () => {
    vi.mocked(getSubtaskMessages).mockResolvedValue(mkResult())
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_dev_a1', sessionId: 's-1', agent: 'dev',
               description: '修改任务', status: 'completed', depth: 1,
               fetchFn: getSubtaskMessages },
    })
    await wrapper.find('.subtask-bubble__head').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.subtask-bubble__reuse').text()).toContain('已复用·2 段')
    const text = wrapper.text()
    expect(text).toContain('任务段 1')
    expect(text).toContain('任务段 2')
    expect(text).toContain('第 4 轮派发')          // turn=3 → +1 展示
    expect(text).toContain('本段任务输入')
    // 分段头出现在正确位置：任务段 2 的标签是 task two
    expect(wrapper.text()).toContain('task two')
  })

  it('单段会话（无复用）不显示复用徽标，user 消息保持「委托输入」', async () => {
    const single = mkResult()
    single.subtask.segments = [mkSegments()[0]]
    vi.mocked(getSubtaskMessages).mockResolvedValue(single)
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_dev_a1', sessionId: 's-1', agent: 'dev',
               description: 'do x', status: 'completed', depth: 1,
               fetchFn: getSubtaskMessages },
    })
    await wrapper.find('.subtask-bubble__head').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.subtask-bubble__reuse').exists()).toBe(false)
    expect(wrapper.text()).toContain('委托输入')
    expect(wrapper.text()).not.toContain('已复用')
  })

  it('无 segments 数据（旧数据）不渲染边界，行为不变', async () => {
    const legacy = mkResult()
    delete (legacy.subtask as any).segments
    vi.mocked(getSubtaskMessages).mockResolvedValue(legacy)
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_dev_a1', sessionId: 's-1', agent: 'dev',
               description: 'do x', status: 'completed', depth: 1,
               fetchFn: getSubtaskMessages },
    })
    await wrapper.find('.subtask-bubble__head').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.subtask-bubble__reuse').exists()).toBe(false)
    expect(wrapper.find('.subtask-bubble__segment').exists()).toBe(false)
    expect(wrapper.text()).toContain('委托输入')
  })
})
