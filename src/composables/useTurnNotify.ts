/**
 * F9 长任务完成——浏览器系统通知（Notification API）。
 *
 * 与站内通知（铃铛，服务端落库）互补：当回合完成时，只要页面标签被切到后台
 * /窗口失焦，就弹一条操作系统级 Toast，用户无需盯着页面。点击 Toast 聚焦窗口。
 *
 * 门控（见 shouldNotifyTurn 纯函数）：
 *   1) 功能已开启且浏览器授权（granted）；
 *   2) 页面不可见或窗口未聚焦（用户正在看就不打扰）；
 *   3) 回合耗时 ≥ 阈值（默认 30s，与服务端站内通知阈值一致）。
 * 另外附带：完成时播放短促提示音（可独立开关）、Tab 标题加未读角标「(N) …」，
 * 重新可见/聚焦时自动清除角标。
 */
import { ref, computed, onUnmounted, getCurrentInstance } from 'vue'

export const TURN_NOTIFY_MIN_MS = 30_000

const LS_BROWSER = 'ai-chat.notify.browser'
const LS_SOUND = 'ai-chat.notify.sound'
const NOTIF_TAG = 'ai-chat-turn-done'

export interface TurnNotifyInput {
  enabled: boolean
  permission: string // Notification.permission：granted / denied / default / unsupported
  hidden: boolean   // document.hidden
  focused: boolean  // document.hasFocus()
  durationMs: number
  threshold?: number
}

/** 纯门控：决定一次回合完成是否应弹出系统通知（便于单测）。 */
export function shouldNotifyTurn(i: TurnNotifyInput): boolean {
  if (!i.enabled) return false
  if (i.permission !== 'granted') return false
  // 页面可见且窗口有焦点 = 用户正在看，无需系统通知
  if (!i.hidden && i.focused) return false
  if (i.durationMs < (i.threshold ?? TURN_NOTIFY_MIN_MS)) return false
  return true
}

function lsGet(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : v === '1'
  } catch {
    return fallback
  }
}

function lsSet(key: string, v: boolean) {
  try {
    localStorage.setItem(key, v ? '1' : '0')
  } catch {
    /* private mode / unavailable storage — ignore */
  }
}

export function useTurnNotify() {
  const supported = typeof window !== 'undefined' && 'Notification' in window
  const permission = ref<string>(
    supported ? Notification.permission : 'unsupported',
  )
  // 默认开启意图；真正能否弹取决于授权。
  const browserEnabled = ref(lsGet(LS_BROWSER, true))
  const soundEnabled = ref(lsGet(LS_SOUND, true))
  const titleUnread = ref(0)

  const canBrowserNotify = computed(
    () => supported && permission.value === 'granted' && browserEnabled.value,
  )

  /** 首次开启时（用户点击手势内）请求系统授权。返回最终权限状态。 */
  async function enableBrowserNotify(): Promise<string> {
    browserEnabled.value = true
    lsSet(LS_BROWSER, true)
    if (supported && Notification.permission === 'default') {
      try {
        permission.value = await Notification.requestPermission()
      } catch {
        permission.value = Notification.permission
      }
    } else if (supported) {
      permission.value = Notification.permission
    }
    return permission.value
  }

  function disableBrowserNotify() {
    browserEnabled.value = false
    lsSet(LS_BROWSER, false)
  }

  function toggleSound() {
    soundEnabled.value = !soundEnabled.value
    lsSet(LS_SOUND, soundEnabled.value)
  }

  // ---- 提示音（WebAudio，失败静默）-------------------------------------
  let audioCtx: AudioContext | null = null
  function playChime() {
    try {
      const AC = window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!AC) return
      if (!audioCtx) audioCtx = new AC()
      if (audioCtx.state === 'suspended') void audioCtx.resume()
      const t0 = audioCtx.currentTime
      // 两声短促上扬「叮咚」
      const notes: Array<[number, number]> = [[880, 0], [1174.66, 0.12]]
      for (const [freq, offset] of notes) {
        const osc = audioCtx.createOscillator()
        const gain = audioCtx.createGain()
        osc.type = 'sine'
        osc.frequency.value = freq
        const start = t0 + offset
        gain.gain.setValueAtTime(0.0001, start)
        gain.gain.exponentialRampToValueAtTime(0.18, start + 0.02)
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.14)
        osc.connect(gain).connect(audioCtx.destination)
        osc.start(start)
        osc.stop(start + 0.16)
      }
    } catch {
      /* 自动播放策略等原因失败时静默 */
    }
  }

  // ---- Tab 标题未读角标 -------------------------------------------------
  let baseTitle = ''
  function renderTitle() {
    try {
      if (!baseTitle) baseTitle = document.title.replace(/^\(\d+\)\s*/, '')
      document.title = titleUnread.value > 0 ? `(${titleUnread.value}) ${baseTitle}` : baseTitle
    } catch {
      /* ignore */
    }
  }
  function bumpTitle() {
    titleUnread.value += 1
    renderTitle()
  }
  function clearTitle() {
    if (titleUnread.value === 0) return
    titleUnread.value = 0
    renderTitle()
  }

  function onVisibility() {
    if (!document.hidden && document.hasFocus()) clearTitle()
  }

  /**
   * 回合完成时调用。返回是否弹出了系统通知（便于测试/调试）。
   * @param title   会话标题（用于通知正文）
   * @param onOpen  点击通知时回调（通常聚焦窗口/定位会话）
   */
  function notifyTurnComplete(
    title: string,
    durationMs: number,
    onOpen?: () => void,
  ): boolean {
    const hidden = document.hidden
    const focused = typeof document.hasFocus === 'function' ? document.hasFocus() : !hidden
    if (!shouldNotifyTurn({
      enabled: browserEnabled.value,
      permission: permission.value,
      hidden,
      focused,
      durationMs,
    })) {
      return false
    }

    // 系统通知（同 tag 聚合，避免多回合刷屏）
    if (supported) {
      try {
        const secs = Math.round(durationMs / 1000)
        const dur = secs >= 60 ? `${Math.floor(secs / 60)} 分 ${secs % 60} 秒` : `${secs} 秒`
        const n = new Notification('AI 助手任务完成', {
          body: `会话：${title || '新会话'}（耗时 ${dur}），点击查看`,
          tag: NOTIF_TAG,
        })
        n.onclick = () => {
          try { window.focus() } catch { /* ignore */ }
          onOpen?.()
          n.close()
        }
      } catch {
        /* 某些浏览器要求通过 ServiceWorker 显示，静默降级 */
      }
    }

    if (soundEnabled.value) playChime()
    bumpTitle()
    return true
  }

  document.addEventListener('visibilitychange', onVisibility)
  window.addEventListener('focus', onVisibility)
  // 仅在组件 setup 中调用时挂卸载清理（便于在非组件环境复用/测试）。
  if (getCurrentInstance()) {
    onUnmounted(() => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('focus', onVisibility)
      clearTitle()
    })
  }

  return {
    supported,
    permission,
    browserEnabled,
    soundEnabled,
    titleUnread,
    canBrowserNotify,
    enableBrowserNotify,
    disableBrowserNotify,
    toggleSound,
    notifyTurnComplete,
    clearTitle,
  }
}
