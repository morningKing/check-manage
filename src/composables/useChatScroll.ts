/**
 * 流式聊天的智能滚动（对标 Claude TUI 的 auto-scroll gating）。
 *
 * 问题：流式输出时每次内容增量都 setScrollTop(到底)，用户一旦上滑回看历史，
 * 就会被新内容反复拽回底部。
 *
 * 策略：维护一个「贴底（pinned）」状态——
 * - scroll 事件里实时判断视口是否在底部阈值内，是则 pinned=true 并清零未读；
 * - 仅当 pinned=true 时，消息增量才自动滚到底部（rAF + nextTick 合并调度，
 *   等 Markdown/流式 DOM 落定后再滚，最多二次校准）；
 * - 用户上滑（pinned=false）时不再强制滚动，期间新消息累计为 unread，
 *   由调用方展示「↓ N 条新消息」浮动按钮；
 * - 用户主动回到底部 / 发送消息 / 切换会话时调用 pinToBottom() 强制吸附。
 */
import { nextTick, ref } from 'vue'

/** 距底部小于该像素数即视为「贴底」。 */
export const SCROLL_BOTTOM_THRESHOLD = 80

interface ScrollMetrics {
  scrollHeight: number
  scrollTop: number
  clientHeight: number
}

export function distanceFromBottom(el: ScrollMetrics): number {
  return Math.max(0, el.scrollHeight - el.scrollTop - el.clientHeight)
}

/** 距底阈值内返回 true；元素缺失时默认视为贴底（不抑制自动滚动）。 */
export function isNearBottom(el: ScrollMetrics | null | undefined,
                             threshold = SCROLL_BOTTOM_THRESHOLD): boolean {
  if (!el) return true
  return distanceFromBottom(el) <= threshold
}

export function useChatScroll(
  getScrollEl: () => HTMLElement | null | undefined,
  threshold = SCROLL_BOTTOM_THRESHOLD,
) {
  /** 视口是否吸附在底部（新内容到达时是否自动跟随）。 */
  const pinned = ref(true)
  /** 用户上滑期间新增的消息条数（浮动按钮角标）。 */
  const unread = ref(0)
  let rafId: number | null = null

  function setScrollTop() {
    const el = getScrollEl()
    if (el) el.scrollTop = el.scrollHeight
  }

  /** 同一帧内多次触发只滚一次（流式 delta 频率很高）。 */
  function scheduleScroll() {
    if (rafId != null) return
    rafId = requestAnimationFrame(() => {
      rafId = null
      void nextTick(setScrollTop)
    })
  }

  /** 绑定到滚动容器的 scroll 事件：据此更新 pinned。 */
  function onScroll() {
    const el = getScrollEl()
    if (isNearBottom(el, threshold)) {
      pinned.value = true
      unread.value = 0
    } else {
      pinned.value = false
    }
  }

  /** 新消息到达：贴底则跟随滚动，否则累计未读。 */
  function onNewMessage() {
    if (pinned.value) scheduleScroll()
    else unread.value += 1
  }

  /** 流式文本增量（同一消息内增长）：贴底才滚，不改变未读计数。 */
  function onStreamDelta() {
    if (pinned.value) scheduleScroll()
  }

  /** 强制滚到底部并恢复贴底（发送消息 / 运行脚本 / 点击回到底部 / 切换会话）。 */
  async function pinToBottom() {
    pinned.value = true
    unread.value = 0
    await nextTick()
    setScrollTop()
    // Markdown / 图片表格渲染后高度还会变一次，下一帧再校准。
    requestAnimationFrame(setScrollTop)
  }

  /** 切换会话时重置为初始贴底态。 */
  function reset() {
    pinned.value = true
    unread.value = 0
  }

  return { pinned, unread, onScroll, onNewMessage, onStreamDelta, pinToBottom, reset }
}
