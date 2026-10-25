import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

vi.mock('@/api/takeover', () => ({
  getTakeoverSession: vi.fn(),
  getTakeoverMessages: vi.fn(),
  sendTakeoverMessage: vi.fn(),
}))
import * as api from '@/api/takeover'
import TakeoverSessionPage from '../TakeoverSessionPage.vue'

const TOKEN = 'tok-1'

const sessionInfo = {
  batchName: 'Q3报表批次',
  batchSeq: 7,
  inputFile: 'input.xlsx',
  title: '季度报表任务',
  childStatus: 'paused',
  batchStatus: 'running',
  expiresAt: '2026-10-26T18:00:00',
}

const messagesPayload = (childStatus = 'paused', messages: any[] = []) => ({
  childStatus,
  messages,
  total: messages.length,
  truncated: false,
})

// MarkdownView 是重组件（md-editor-v3），页面测试里换成透传 text 的轻 stub
const MdStub = { template: '<div class="mdv-stub">{{ text }}</div>', props: ['text'] }

async function mountAt(query = `?t=${TOKEN}`) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/takeover/session', component: TakeoverSessionPage },
    ],
  })
  router.push(`/takeover/session${query}`)
  await router.isReady()
  const w = mount(TakeoverSessionPage, {
    global: { plugins: [router], stubs: { MarkdownView: MdStub } },
  })
  await flushPromises()
  return w
}

const setDocumentHidden = (hidden: boolean) => {
  Object.defineProperty(document, 'hidden', { value: hidden, configurable: true })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getTakeoverSession).mockResolvedValue(sessionInfo)
  vi.mocked(api.getTakeoverMessages).mockResolvedValue(messagesPayload())
  vi.mocked(api.sendTakeoverMessage).mockResolvedValue({ queued: true })
})

afterEach(() => {
  vi.useRealTimers()
  setDocumentHidden(false)
})

describe('TakeoverSessionPage', () => {
  it('missing token renders error card without any API call', async () => {
    const w = await mountAt('')
    expect(w.find('[data-test="error-card"]').exists()).toBe(true)
    expect(w.text()).toContain('链接无效或已过期')
    expect(api.getTakeoverSession).not.toHaveBeenCalled()
    expect(api.getTakeoverMessages).not.toHaveBeenCalled()
    expect(api.sendTakeoverMessage).not.toHaveBeenCalled()
    w.unmount()
  })

  it('renders batch header and history messages', async () => {
    const msgs = [
      { id: 'm1', role: 'user', content: '帮我跑一下报表', createdAt: null },
      {
        id: 'm2',
        role: 'assistant',
        content: [
          { type: 'reasoning', text: '思考中……' },
          { type: 'text', text: '好的，**报表**已生成。' },
          { type: 'tool_use', name: 'run_sql' },
        ],
        createdAt: null,
      },
      { id: 'm3', role: 'system', content: 'internal', createdAt: null },
    ]
    vi.mocked(api.getTakeoverMessages).mockResolvedValue(messagesPayload('paused', msgs))

    const w = await mountAt()
    expect(api.getTakeoverSession).toHaveBeenCalledWith(TOKEN)
    expect(api.getTakeoverMessages).toHaveBeenCalledWith(TOKEN)

    // 顶栏：批名 + seq + 输入文件 + 状态徽标
    expect(w.text()).toContain('Q3报表批次')
    expect(w.text()).toContain('#7')
    expect(w.text()).toContain('input.xlsx')
    expect(w.text()).toContain('已暂停（可接管）')

    // user 气泡渲染原文本
    const userBubble = w.find('.msg--user .msg__bubble')
    expect(userBubble.exists()).toBe(true)
    expect(userBubble.text()).toBe('帮我跑一下报表')

    // assistant 的 text part 走 MarkdownView（此处为 stub）
    const mdStubs = w.findAll('.mdv-stub')
    expect(mdStubs).toHaveLength(1)
    expect(mdStubs[0].text()).toBe('好的，**报表**已生成。')

    // reasoning / tool part 折叠为一行灰色标记
    const skips = w.findAll('.msg__skip')
    expect(skips).toHaveLength(1)
    expect(skips[0].text()).toBe('（内部过程，略）')

    // 其余 role（system）跳过
    expect(w.findAll('.msg')).toHaveLength(2)

    // token 不落 localStorage
    expect(localStorage.setItem).not.toHaveBeenCalled()
    w.unmount()
  })

  it('running childStatus disables input with busy hint', async () => {
    vi.mocked(api.getTakeoverMessages).mockResolvedValue(messagesPayload('running'))
    const w = await mountAt()
    const textarea = w.find('textarea')
    expect(textarea.attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('任务执行中')
    w.unmount()
  })

  it('paused allows sending; success clears input and polls messages immediately', async () => {
    const w = await mountAt()
    const textarea = w.find('textarea')
    expect(textarea.attributes('disabled')).toBeUndefined()

    await textarea.setValue('请重新核对数字')
    await textarea.trigger('keydown.enter')
    await flushPromises()

    expect(api.sendTakeoverMessage).toHaveBeenCalledWith(TOKEN, '请重新核对数字')
    expect((w.find('textarea').element as HTMLTextAreaElement).value).toBe('')
    // 初始加载 1 次 + 发送成功后立即再拉 1 次
    expect(vi.mocked(api.getTakeoverMessages).mock.calls.length).toBe(2)
    w.unmount()
  })

  it('completed childStatus still allows sending', async () => {
    vi.mocked(api.getTakeoverMessages).mockResolvedValue(messagesPayload('completed'))
    const w = await mountAt()
    expect(w.find('textarea').attributes('disabled')).toBeUndefined()
    await w.find('textarea').setValue('补充一点')
    await w.find('textarea').trigger('keydown.enter')
    await flushPromises()
    expect(api.sendTakeoverMessage).toHaveBeenCalledWith(TOKEN, '补充一点')
    w.unmount()
  })

  it('409 on send surfaces the backend message inline, keeps input', async () => {
    vi.mocked(api.sendTakeoverMessage).mockRejectedValueOnce({
      response: {
        status: 409,
        data: { error: { code: 'CHILD_RUNNING', message: '任务执行中，请外部系统先暂停后再接管' } },
      },
    })
    const w = await mountAt()
    await w.find('textarea').setValue('抢一下控制权')
    await w.find('textarea').trigger('keydown.enter')
    await flushPromises()

    const warning = w.find('[data-test="conflict-warning"]')
    expect(warning.exists()).toBe(true)
    expect(warning.text()).toBe('任务执行中，请外部系统先暂停后再接管')
    // 输入保留便于重试，且不升级为整页错误卡
    expect((w.find('textarea').element as HTMLTextAreaElement).value).toBe('抢一下控制权')
    expect(w.find('[data-test="error-card"]').exists()).toBe(false)
    w.unmount()
  })

  it('404 on load renders full error card', async () => {
    vi.mocked(api.getTakeoverSession).mockRejectedValue({
      response: { status: 404, data: { error: { code: 'NOT_FOUND', message: 'session not found' } } },
    })
    const w = await mountAt()
    expect(w.find('[data-test="error-card"]').exists()).toBe(true)
    expect(w.find('textarea').exists()).toBe(false)
    w.unmount()
  })

  it('polls messages every 3s, skips while hidden, resumes on visible, cleans up on unmount', async () => {
    // fake timers 必须在 mount 之前装好，轮询 interval 才会被 fake 时钟接管
    vi.useFakeTimers()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/takeover/session', component: TakeoverSessionPage },
      ],
    })
    router.push(`/takeover/session?t=${TOKEN}`)
    await router.isReady()
    const w = mount(TakeoverSessionPage, {
      global: { plugins: [router], stubs: { MarkdownView: MdStub } },
    })
    // fake timers 下用 advanceTimersByTimeAsync 冲刷初始加载的 promise 链
    await vi.advanceTimersByTimeAsync(0)

    const calls = () => vi.mocked(api.getTakeoverMessages).mock.calls.length
    const countAfterLoad = calls()
    expect(countAfterLoad).toBe(1)

    // 3s 一轮
    await vi.advanceTimersByTimeAsync(3000)
    expect(calls()).toBe(countAfterLoad + 1)
    await vi.advanceTimersByTimeAsync(3000)
    expect(calls()).toBe(countAfterLoad + 2)

    // 页面不可见时跳过
    setDocumentHidden(true)
    await vi.advanceTimersByTimeAsync(6000)
    expect(calls()).toBe(countAfterLoad + 2)

    // 回到前台：visibilitychange 立即补拉一次
    setDocumentHidden(false)
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(calls()).toBe(countAfterLoad + 3)

    // 卸载后：定时器与 visibilitychange 监听都已清理
    w.unmount()
    await vi.advanceTimersByTimeAsync(9000)
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(calls()).toBe(countAfterLoad + 3)
  })
})
