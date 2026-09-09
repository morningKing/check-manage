import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useTurnNotify, shouldNotifyTurn, TURN_NOTIFY_MIN_MS } from '../useTurnNotify'

describe('shouldNotifyTurn（纯门控）', () => {
  const base = {
    enabled: true,
    permission: 'granted',
    hidden: true,
    focused: false,
    durationMs: 60_000,
  }

  it('功能关闭时不通知', () => {
    expect(shouldNotifyTurn({ ...base, enabled: false })).toBe(false)
  })

  it('未授权（default / denied / unsupported）时不通知', () => {
    expect(shouldNotifyTurn({ ...base, permission: 'default' })).toBe(false)
    expect(shouldNotifyTurn({ ...base, permission: 'denied' })).toBe(false)
    expect(shouldNotifyTurn({ ...base, permission: 'unsupported' })).toBe(false)
  })

  it('页面可见且窗口聚焦（用户正在看）时不通知', () => {
    expect(shouldNotifyTurn({ ...base, hidden: false, focused: true })).toBe(false)
  })

  it('页面隐藏 + 长耗时 → 通知', () => {
    expect(shouldNotifyTurn({ ...base, hidden: true, focused: false })).toBe(true)
  })

  it('标签可见但窗口失焦（在看别的窗口）→ 通知', () => {
    expect(shouldNotifyTurn({ ...base, hidden: false, focused: false })).toBe(true)
  })

  it('耗时低于阈值不通知；阈值边界可用自定义', () => {
    expect(shouldNotifyTurn({ ...base, durationMs: TURN_NOTIFY_MIN_MS - 1 })).toBe(false)
    expect(shouldNotifyTurn({ ...base, durationMs: TURN_NOTIFY_MIN_MS })).toBe(true)
    expect(shouldNotifyTurn({ ...base, durationMs: 5_000, threshold: 3_000 })).toBe(true)
  })
})

describe('useTurnNotify（composable）', () => {
  let notifInstances: Array<{ title: string; options: any; onClick: (() => void) | null; close: () => void }>
  class FakeNotification {
    static permission = 'granted'
    static requestPermission = vi.fn(async () => 'granted')
    title: string
    options: any
    onClick: (() => void) | null = null
    constructor(title: string, options?: NotificationOptions) {
      this.title = title
      this.options = options || {}
      notifInstances.push(this as any)
    }
    set onclick(fn: (() => void) | null) { this.onClick = fn }
    close() { /* noop */ }
  }

  beforeEach(() => {
    notifInstances = []
    localStorage.clear()
    document.title = 'AI 助手 - check-manage'
    Object.defineProperty(document, 'hidden', { value: true, configurable: true, writable: true })
    document.hasFocus = vi.fn(() => false)
    ;(globalThis as any).Notification = FakeNotification
    vi.spyOn(window, 'focus').mockImplementation(() => {})
  })

  afterEach(() => {
    delete (globalThis as any).Notification
    vi.restoreAllMocks()
  })

  it('不支持 Notification 时 supported=false（jsdom 默认）', () => {
    delete (globalThis as any).Notification
    const t = useTurnNotify()
    expect(t.supported).toBe(false)
  })

  it('浏览器通知：后台长回合 → 弹出系统通知并加 Tab 标题角标', () => {
    const t = useTurnNotify()
    expect(t.supported).toBe(true)
    const opened = vi.fn()
    const fired = t.notifyTurnComplete('auth 重构讨论', 65_000, opened)
    expect(fired).toBe(true)
    expect(notifInstances).toHaveLength(1)
    const n = notifInstances[0]
    expect(n.title).toBe('AI 助手任务完成')
    expect(n.options.body).toContain('auth 重构讨论')
    expect(n.options.tag).toBeTruthy()  // 同 tag 聚合
    expect(document.title.startsWith('(1) ')).toBe(true)

    // 点击通知 → 聚焦窗口并触发回调
    n.onClick?.()
    expect(window.focus).toHaveBeenCalled()
    expect(opened).toHaveBeenCalled()
  })

  it('页面可见且聚焦时不弹通知、不改标题', () => {
    Object.defineProperty(document, 'hidden', { value: false, configurable: true, writable: true })
    document.hasFocus = vi.fn(() => true)
    const t = useTurnNotify()
    expect(t.notifyTurnComplete('x', 999_000)).toBe(false)
    expect(notifInstances).toHaveLength(0)
    expect(document.title).toBe('AI 助手 - check-manage')
  })

  it('短回合不弹通知', () => {
    const t = useTurnNotify()
    expect(t.notifyTurnComplete('x', 5_000)).toBe(false)
    expect(notifInstances).toHaveLength(0)
  })

  it('enableBrowserNotify：default 状态会请求授权并持久化开关', async () => {
    FakeNotification.permission = 'default'
    FakeNotification.requestPermission.mockClear()
    const t = useTurnNotify()
    const result = await t.enableBrowserNotify()
    expect(result).toBe('granted')
    expect(FakeNotification.requestPermission).toHaveBeenCalled()
    expect(localStorage.getItem('ai-chat.notify.browser')).toBe('1')
    expect(t.browserEnabled.value).toBe(true)
    FakeNotification.permission = 'granted'
  })

  it('关闭开关后不再弹通知', () => {
    const t = useTurnNotify()
    t.disableBrowserNotify()
    expect(localStorage.getItem('ai-chat.notify.browser')).toBe('0')
    expect(t.notifyTurnComplete('x', 999_000)).toBe(false)
    expect(notifInstances).toHaveLength(0)
  })

  it('提示音开关持久化', () => {
    const t = useTurnNotify()
    expect(t.soundEnabled.value).toBe(true)
    t.toggleSound()
    expect(t.soundEnabled.value).toBe(false)
    expect(localStorage.getItem('ai-chat.notify.sound')).toBe('0')
  })

  it('多次完成累加标题角标，clearTitle 还原', () => {
    const t = useTurnNotify()
    t.notifyTurnComplete('a', 60_000)
    t.notifyTurnComplete('b', 60_000)
    expect(document.title.startsWith('(2) ')).toBe(true)
    t.clearTitle()
    expect(document.title).toBe('AI 助手 - check-manage')
  })
})
