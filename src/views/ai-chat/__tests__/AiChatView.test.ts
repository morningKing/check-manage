import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { batchStatusLabel, isBatchChildAutomatic } from '../batchChildState'
import { shouldSyncAiChatSession } from '../aiChatRoute'
import TraceLink from '@/components/ai-chat/TraceLink.vue'

describe('AiChatView batch child controls', () => {
  it.each(['pending', 'running'] as const)('hides the composer while child is %s', (status) => {
    expect(isBatchChildAutomatic(status)).toBe(true)
  })

  it.each(['completed', 'failed', 'cancelled'] as const)('exposes the composer after child is %s', (status) => {
    expect(isBatchChildAutomatic(status)).toBe(false)
  })

  it('labels partial and cancelled terminal states', () => {
    expect(batchStatusLabel('partial')).toBe('部分失败')
    expect(batchStatusLabel('cancelled')).toBe('已取消')
  })

  it('syncs the selected session when the notification query changes', () => {
    expect(shouldSyncAiChatSession('session-2', 'session-1')).toBe(true)
    expect(shouldSyncAiChatSession('session-1', 'session-1')).toBe(false)
    expect(shouldSyncAiChatSession(undefined, 'session-1')).toBe(false)
  })

  it('renders an accessible external trace anchor and hides it without metadata', () => {
    const trace = {
      traceId: '0123456789abcdef0123456789abcdef',
      traceUrl: 'https://langfuse.example/project/project-1/traces/0123456789abcdef0123456789abcdef',
      isSampled: true,
    }
    const wrapper = mount(TraceLink, { props: { trace } })
    const anchor = wrapper.get('a')
    expect(anchor.attributes('href')).toBe(trace.traceUrl)
    expect(anchor.attributes('target')).toBe('_blank')
    expect(anchor.attributes('rel')).toBe('noopener noreferrer')
    expect(anchor.attributes('aria-label')).toBe('查看 Trace')
    expect(anchor.text()).toBe('查看 Trace')

    const hidden = mount(TraceLink, { props: { trace: undefined } })
    expect(hidden.find('a').exists()).toBe(false)
  })
})
