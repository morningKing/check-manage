import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { useChatScroll, distanceFromBottom, isNearBottom, SCROLL_BOTTOM_THRESHOLD } from '../useChatScroll'

function makeEl(m: { scrollHeight?: number; scrollTop?: number; clientHeight?: number } = {}) {
  const el = {
    scrollHeight: m.scrollHeight ?? 1000,
    clientHeight: m.clientHeight ?? 400,
    scrollTop: m.scrollTop ?? 0,
  }
  return el as unknown as HTMLElement & { scrollTop: number }
}

describe('distanceFromBottom / isNearBottom', () => {
  it('计算距底距离（不为负）', () => {
    // scrollHeight 1000, clientHeight 400, scrollTop 550 -> 距底 50
    expect(distanceFromBottom(makeEl({ scrollTop: 550 }) as any)).toBe(50)
    // 滚动超过底部也夹到 0
    expect(distanceFromBottom(makeEl({ scrollTop: 9999 }) as any)).toBe(0)
  })

  it('阈值内视为贴底，超出视为离开', () => {
    expect(isNearBottom(makeEl({ scrollTop: 550 }) as any)).toBe(true)   // 距 50
    expect(isNearBottom(makeEl({ scrollTop: 100 }) as any)).toBe(false)  // 距 500
  })

  it('元素缺失时默认贴底（不抑制滚动）', () => {
    expect(isNearBottom(null)).toBe(true)
    expect(isNearBottom(undefined)).toBe(true)
  })

  it('自定义阈值生效', () => {
    // 距底 50，默认阈值 80 内 -> true；阈值 40 外 -> false
    expect(isNearBottom(makeEl({ scrollTop: 550 }) as any, 40)).toBe(false)
    expect(SCROLL_BOTTOM_THRESHOLD).toBe(80)
  })
})

describe('useChatScroll 状态机', () => {
  beforeEach(() => {
    // rAF 同步执行，保证 nextTick 内的滚动可在测试中观测
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 1 })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('初始为贴底、无未读', () => {
    const el = makeEl()
    const s = useChatScroll(() => el)
    expect(s.pinned.value).toBe(true)
    expect(s.unread.value).toBe(0)
  })

  it('onScroll：上滑离开底部 -> pinned=false；滚回 -> pinned=true 且清零未读', () => {
    const el = makeEl({ scrollTop: 100 })  // 距底 500
    const s = useChatScroll(() => el)
    s.onScroll()
    expect(s.pinned.value).toBe(false)

    // 上滑期间来一条新消息 -> 累计未读
    s.onNewMessage()
    expect(s.unread.value).toBe(1)

    // 滚回贴底（距 50）-> 自动清零
    ;(el as any).scrollTop = 550
    s.onScroll()
    expect(s.pinned.value).toBe(true)
    expect(s.unread.value).toBe(0)
  })

  it('贴底时新消息：自动滚到底且不累计未读', async () => {
    const el = makeEl({ scrollTop: 0 })
    const s = useChatScroll(() => el)
    s.onNewMessage()
    await nextTick()
    expect(s.unread.value).toBe(0)
    expect(el.scrollTop).toBe(1000)  // 滚到 scrollHeight
  })

  it('离开底部时新消息：不强制滚动，累计未读', () => {
    const el = makeEl({ scrollTop: 100 })
    const s = useChatScroll(() => el)
    s.onScroll()  // 标记离开
    el.scrollTop = 100
    s.onNewMessage()
    s.onNewMessage()
    expect(s.pinned.value).toBe(false)
    expect(s.unread.value).toBe(2)
    expect(el.scrollTop).toBe(100)  // 未被拽走
  })

  it('流式增量：贴底才滚，且不改变未读计数', () => {
    const el = makeEl({ scrollTop: 100 })
    const s = useChatScroll(() => el)
    s.onScroll()  // 离开底部
    s.onStreamDelta()
    s.onStreamDelta()
    expect(el.scrollTop).toBe(100)   // 不跟随
    expect(s.unread.value).toBe(0)   // 同一消息内的 delta 不计未读
  })

  it('pinToBottom：强制回到底、恢复贴底、清零未读', async () => {
    const el = makeEl({ scrollTop: 100 })
    const s = useChatScroll(() => el)
    s.onScroll()
    s.onNewMessage()
    expect(s.unread.value).toBe(1)

    await s.pinToBottom()
    expect(s.pinned.value).toBe(true)
    expect(s.unread.value).toBe(0)
    expect(el.scrollTop).toBe(1000)
  })

  it('reset：恢复初始贴底态', () => {
    const el = makeEl({ scrollTop: 100 })
    const s = useChatScroll(() => el)
    s.onScroll()
    s.onNewMessage()
    s.reset()
    expect(s.pinned.value).toBe(true)
    expect(s.unread.value).toBe(0)
  })
})
