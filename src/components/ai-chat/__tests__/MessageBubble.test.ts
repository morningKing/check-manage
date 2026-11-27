/**
 * MessageBubble.vue（从 AiChatView 拆出的单条消息气泡）测试。
 *
 * 覆盖：part 类型分发渲染（text/tool_use=todo/query/reasoning/error/artifact）、
 * meta 行、操作按钮回调透传、流式期间编辑/重试禁用。
 * 重子组件（MarkdownView/Thinking/SubtaskBubble 等）用可识别 stub，只验证
 * 「哪条分支被渲染 + 关键 props 正确」——分支判定逻辑（prepared computed）
 * 在本组件内，是本次拆分的核心改动。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import MessageBubble from '@/components/ai-chat/MessageBubble.vue'
import type { AiMessage } from '@/api/aiChat'

const stubs = {
  ElIcon: { template: '<i><slot/></i>' },
  MarkdownView: { props: ['text'], template: '<div class="stub-md">{{ text }}</div>' },
  ArtifactCard: {
    props: ['lang', 'code', 'versions', 'running'],
    emits: ['preview', 'run'],
    template: '<div class="stub-artifact" :data-versions="versions">{{ code }}</div>',
  },
  TodoListBlock: { props: ['todos'], template: '<div class="stub-todo"/>' },
  QueryResultBlock: { props: ['result'], template: '<div class="stub-query"/>' },
  QuestionResultCard: { props: ['view'], template: '<div class="stub-question"/>' },
  ToolCallBubble: {
    props: ['name', 'title', 'status', 'input', 'result', 'durationMs'],
    template: '<div class="stub-tool" :data-name="name"/>',
  },
  Thinking: { props: ['content'], template: '<div class="stub-thinking"/>' },
  SubtaskBubble: { props: ['subtaskId', 'sessionId'], template: '<div class="stub-subtask"/>' },
  RunResultBlock: { props: ['result'], template: '<div class="stub-run"/>' },
  UserMentionText: { props: ['text'], emits: ['preview'], template: '<div class="stub-mention"/>' },
  ChatFile: { props: ['name'], template: '<div class="stub-file"/>' },
  ElImage: { props: ['src'], template: '<img class="stub-image"/>' },
}

function baseMsg(over: Partial<AiMessage> & { content: any[] }): AiMessage {
  return { id: 'm1', role: 'assistant', createdAt: '', seq: 1, ...over } as AiMessage
}

function mountBubble(message: AiMessage, props: Record<string, unknown> = {}) {
  return mount(MessageBubble, {
    props: {
      message,
      sessionId: 'sess_1',
      streaming: false,
      mentionFiles: [],
      runningCode: null,
      getVersions: () => [{ lang: 'python', code: '' }, { lang: 'python', code: '' }],
      fetchSubtaskMessages: vi.fn(),
      onPreview: vi.fn(),
      onRun: vi.fn(),
      onMentionPreview: vi.fn(),
      onCopy: vi.fn(),
      onEdit: vi.fn(),
      onRetryUser: vi.fn(),
      onRetryAssistant: vi.fn(),
      onFeedback: vi.fn(),
      ...props,
    },
    global: { stubs },
  })
}

describe('MessageBubble.vue', () => {
  it('assistant 文本消息 → MarkdownView 渲染正文', () => {
    const w = mountBubble(baseMsg({ content: [{ type: 'text', text: '你好，世界' }] }))
    expect(w.find('.stub-md').text()).toBe('你好，世界')
    expect(w.find('.msg__role').text()).toBe('AI 助手')
  })

  it('todowrite 工具 part → TodoListBlock（单次解析，不落通用工具气泡）', () => {
    const w = mountBubble(baseMsg({
      content: [{
        type: 'tool_use', name: 'todowrite', status: 'completed',
        input: { todos: [{ content: '步骤一', status: 'completed' }] },
      }],
    }))
    expect(w.find('.stub-todo').exists()).toBe(true)
    expect(w.find('.stub-tool').exists()).toBe(false)
  })

  it('query_collection 结果 → QueryResultBlock；普通工具 → ToolCallBubble', () => {
    const w = mountBubble(baseMsg({ content: [
      { type: 'tool_use', name: 'query_collection', status: 'completed',
        input: {}, result: { mode: 'rows', collection: '订单', total: 3 } },
      { type: 'tool_use', name: 'bash', status: 'completed', input: { command: 'ls' }, result: 'ok' },
    ] }))
    expect(w.findAll('.stub-query')).toHaveLength(1)
    expect(w.findAll('.stub-tool')).toHaveLength(1)
    expect(w.find('.stub-tool').attributes('data-name')).toBe('bash')
  })

  it('reasoning part → Thinking stub；error part → 错误条', () => {
    const w = mountBubble(baseMsg({ content: [
      { type: 'reasoning', text: '想一下' },
      { type: 'error', text: '本轮执行失败：超时' },
    ] }))
    expect(w.find('.stub-thinking').exists()).toBe(true)
    expect(w.find('.msg__turn-error').text()).toContain('本轮执行失败：超时')
  })

  it('assistant 文本含足够大的代码围栏 → ArtifactCard，versions 计数来自 getVersions', () => {
    const code = Array.from({ length: 10 }, (_, i) => `line_${i} = ${i}`).join('\n')
    const w = mountBubble(baseMsg({
      content: [{ type: 'text', text: `说明\n\`\`\`python\n${code}\n\`\`\`\n尾注` }],
    }))
    const card = w.find('.stub-artifact')
    expect(card.exists()).toBe(true)
    expect(card.attributes('data-versions')).toBe('2')
    // 代码前后的文本段照常走 MarkdownView
    expect(w.findAll('.stub-md').length).toBe(2)
  })

  it('meta 行显示耗时/token；点击复制/反馈按钮回调透传消息本体', async () => {
    const onCopy = vi.fn()
    const onFeedback = vi.fn()
    const w = mountBubble(baseMsg({
      content: [{ type: 'text', text: '回答' }],
      meta: { durationMs: 1200, tokensInput: 10, tokensOutput: 20 },
    }), { onCopy, onFeedback })
    expect(w.find('.msg__meta').text()).toContain('⏱')
    await w.find('button[aria-label="复制"]').trigger('click')
    expect(onCopy).toHaveBeenCalledWith(expect.objectContaining({ id: 'm1' }))
    await w.find('[data-test="feedback-up"]').trigger('click')
    expect(onFeedback).toHaveBeenCalledWith(expect.anything(), 1)
  })

  it('streaming 期间编辑/重新生成禁用；user 消息无 AI 助手角色行', () => {
    const w = mountBubble(baseMsg({
      role: 'user',
      content: [{ type: 'text', text: '提问' }],
    }), { streaming: true })
    expect(w.find('.msg__role').exists()).toBe(false)
    expect(w.find('button[aria-label="编辑并重发"]').attributes('disabled')).toBeDefined()
    expect(w.find('button[aria-label="重新生成"]').attributes('disabled')).toBeDefined()
  })
})
