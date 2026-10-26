<script setup lang="ts">
/**
 * 外部任务会话接管页（公开页面，无 AppLayout 壳）
 *
 * 通过链接参数 ?t=<token> 免登录访问批量任务的会话：
 * - token 只存在于链接与内存中，绝不写入 localStorage
 * - 顶栏展示批次信息与子任务状态徽标；子任务 pending/running 时输入禁用
 * - 执行明显偏差时可「中断任务」（两段确认）：协作式取消由 worker 数秒内
 *   收口落 cancelled，之后可直接发纠正消息在原会话上续跑
 * - 消息区每 3s 轮询刷新，页面不可见（document.hidden）时暂停
 * - 409（子任务执行中拒绝写入）→ 输入区上方内联警告；404/网络错 → 整页错误卡
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import {
  cancelTakeoverSession,
  getTakeoverMessages,
  getTakeoverSession,
  sendTakeoverMessage,
  type TakeoverMessage,
  type TakeoverSessionInfo,
} from '@/api/takeover'

const route = useRoute()
const token = String(route.query.t || '')

const POLL_MS = 3000
const BUSY_STATUSES = ['pending', 'running']
// reasoning / 工具调用等内部 part 对外只显示这一行折叠标记
const INTERNAL_PART_MARK = '（内部过程，略）'

const STATUS_META: Record<string, { label: string; cls: string }> = {
  pending: { label: '排队', cls: 'is-pending' },
  running: { label: '执行中', cls: 'is-running' },
  paused: { label: '已暂停（可接管）', cls: 'is-paused' },
  completed: { label: '已完成', cls: 'is-completed' },
  failed: { label: '失败', cls: 'is-failed' },
  cancelled: { label: '已取消', cls: 'is-cancelled' },
  needs_review: { label: '待复核', cls: 'is-needs-review' },
}

const loading = ref(true)
const loadError = ref('')
const session = ref<TakeoverSessionInfo | null>(null)
const messages = ref<TakeoverMessage[]>([])
const childStatus = ref('')
const draft = ref('')
const sending = ref(false)
const conflictMsg = ref('')
const cancelArmed = ref(false)
const cancelling = ref(false)
const cancelNotice = ref('')
const listEl = ref<HTMLElement | null>(null)

let pollTimer: ReturnType<typeof setInterval> | null = null
let disarmTimer: ReturnType<typeof setTimeout> | null = null

const status = computed(() => childStatus.value || session.value?.childStatus || '')
const busy = computed(() => BUSY_STATUSES.includes(status.value))
const statusMeta = computed(() => STATUS_META[status.value] || { label: status.value || '未知', cls: 'is-unknown' })
const visibleMessages = computed(() => messages.value.filter((m) => m.role === 'user' || m.role === 'assistant'))

interface AssistantSegment { kind: 'text' | 'muted'; text: string }

/** user 气泡纯文本：content 为 parts 时取各段 text 拼接 */
function partsText(content: TakeoverMessage['content']): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content.map((p: any) => (typeof p === 'string' ? p : String(p?.text ?? ''))).join('')
  }
  return content == null ? '' : String(content)
}

/** assistant 消息分段：text 段走 MarkdownView，reasoning/工具段折叠为一行标记（连续的只留一行） */
function assistantSegments(m: TakeoverMessage): AssistantSegment[] {
  const content = m.content
  if (typeof content === 'string') return [{ kind: 'text', text: content }]
  if (!Array.isArray(content)) return [{ kind: 'text', text: content == null ? '' : String(content) }]
  const out: AssistantSegment[] = []
  for (const part of content) {
    const type = typeof part === 'string' ? 'text' : String(part?.type ?? 'text')
    if (type === 'text') {
      const text = typeof part === 'string' ? part : String(part?.text ?? '')
      if (text) out.push({ kind: 'text', text })
    } else if (!out.some((s) => s.kind === 'muted')) {
      // reasoning / 工具调用等内部 part 整体折叠为一行标记，不逐个展开
      out.push({ kind: 'muted', text: INTERNAL_PART_MARK })
    }
  }
  return out
}

/** request.ts 对非 2xx reject 的是 axios error，payload 可能是 {error:{code,message}} 或 {message} */
function extractError(err: any): { status: number; message: string } {
  const status = Number(err?.response?.status) || 0
  const raw = err?.response?.data?.error
  const message =
    (typeof raw === 'string' ? raw : raw?.message)
    || err?.response?.data?.message
    || err?.message
    || '请求失败'
  return { status, message }
}

function applyMessages(payload: { childStatus?: string; messages?: TakeoverMessage[] }) {
  messages.value = Array.isArray(payload?.messages) ? payload.messages : []
  if (payload?.childStatus) childStatus.value = payload.childStatus
}

function formatTime(v?: string | null): string {
  if (!v) return ''
  return String(v).replace('T', ' ').slice(0, 16)
}

/** 整页错误卡（404 / 网络错等不可恢复情形），同时停掉轮询 */
function handleFatal(message: string) {
  stopPolling()
  loadError.value = message || '链接无效或已过期'
}

async function poll() {
  if (!token || document.hidden) return
  try {
    applyMessages(await getTakeoverMessages(token))
  } catch (err) {
    // 会话不存在/已过期 → 整页错误卡；其余（网络抖动、5xx 等）视为瞬时
    // 错误静默等下一轮，避免一次后端闪断把整页打没
    const { status: code } = extractError(err)
    if (code === 404) handleFatal('链接无效或已过期')
  }
}

async function load() {
  if (!token) {
    loading.value = false
    loadError.value = '链接无效或已过期'
    return
  }
  try {
    const [info, payload] = await Promise.all([getTakeoverSession(token), getTakeoverMessages(token)])
    session.value = info
    applyMessages({ childStatus: payload?.childStatus || info.childStatus, messages: payload?.messages })
    loading.value = false
    startPolling()
  } catch (err) {
    loading.value = false
    const { status: code, message } = extractError(err)
    handleFatal(code === 404 ? '链接无效或已过期' : message)
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => { void poll() }, POLL_MS)
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function onVisibilityChange() {
  if (!document.hidden) void poll()
}

async function send() {
  const text = draft.value.trim()
  if (!text || busy.value || sending.value || loading.value || loadError.value) return
  conflictMsg.value = ''
  sending.value = true
  try {
    await sendTakeoverMessage(token, text)
    draft.value = ''
    await poll()
  } catch (err) {
    const { status: code, message } = extractError(err)
    if (code === 404) handleFatal('链接无效或已过期')
    else conflictMsg.value = message
  } finally {
    sending.value = false
  }
}

function onEnterKey() { void send() }

// ---------------------------------------------------------------------------
// 中断任务：两段确认（首次点击只武装按钮，4s 内再点才真正发请求），避免
// 公开页上一次误触就把执行中的任务打断。中断后轮询会把徽标刷成「已取消」，
// 输入随即恢复——纠正闭环由用户直接继续发消息完成。
// ---------------------------------------------------------------------------
const cancelLabel = computed(() =>
  cancelling.value ? '中断中…' : (cancelArmed.value ? '再点一次确认中断' : '中断任务'))

function disarmCancel() {
  cancelArmed.value = false
  if (disarmTimer !== null) {
    clearTimeout(disarmTimer)
    disarmTimer = null
  }
}

async function onCancelClick() {
  if (cancelling.value || loading.value || loadError.value) return
  if (!cancelArmed.value) {
    cancelArmed.value = true
    if (disarmTimer !== null) clearTimeout(disarmTimer)
    disarmTimer = setTimeout(disarmCancel, 4000)
    return
  }
  disarmCancel()
  cancelling.value = true
  conflictMsg.value = ''
  try {
    await cancelTakeoverSession(token)
    cancelNotice.value = '已请求中断，正在停止当前执行，稍后可发送纠正消息。'
  } catch (err) {
    const { status: code, message } = extractError(err)
    if (code === 404) handleFatal('链接无效或已过期')
    else conflictMsg.value = message
  } finally {
    cancelling.value = false
  }
}

// 状态离开 busy（worker 已收口为 cancelled/completed/…）后清掉中断提示；
// 又回到 busy（纠正消息触发的下一轮）则不残留旧文案
watch(busy, (isBusy) => {
  if (!isBusy) cancelNotice.value = ''
})

// 新消息到达时把消息区滚到底部
watch(() => visibleMessages.value.length, async () => {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
})

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  void load()
})

onUnmounted(() => {
  stopPolling()
  if (disarmTimer !== null) clearTimeout(disarmTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <div class="takeover-page">
    <div v-if="loading" class="takeover-card takeover-card--flat">
      <p class="takeover-state">加载中…</p>
    </div>

    <div v-else-if="loadError" class="takeover-card takeover-card--flat takeover-error" data-test="error-card">
      <div class="takeover-error__icon">!</div>
      <p class="takeover-error__title">{{ loadError }}</p>
      <p class="takeover-error__hint">请确认链接是否完整，或联系任务发起人重新获取。</p>
    </div>

    <div v-else class="takeover-card">
      <header class="takeover-topbar">
        <div class="takeover-topbar__main">
          <h1 class="takeover-topbar__title">{{ session?.title || session?.batchName || '任务会话接管' }}</h1>
          <p class="takeover-topbar__meta">
            <span class="takeover-topbar__batch">{{ session?.batchName }}</span>
            <span v-if="session?.batchSeq != null" class="takeover-topbar__seq">#{{ session?.batchSeq }}</span>
            <span v-if="session?.inputFile" class="takeover-topbar__file">输入文件：{{ session?.inputFile }}</span>
          </p>
        </div>
        <div class="takeover-topbar__side">
          <span class="takeover-badge" :class="`takeover-badge--${statusMeta.cls.replace('is-', '')}`">{{ statusMeta.label }}</span>
          <span v-if="session?.expiresAt" class="takeover-topbar__expires">有效期至 {{ formatTime(session?.expiresAt) }}</span>
        </div>
      </header>

      <div ref="listEl" class="takeover-messages" data-test="messages">
        <p v-if="!visibleMessages.length" class="takeover-messages__empty">暂无消息，请在下方输入并发送。</p>
        <template v-for="m in visibleMessages" :key="m.id">
          <div v-if="m.role === 'user'" class="msg msg--user">
            <div class="msg__bubble">{{ partsText(m.content) }}</div>
          </div>
          <div v-else class="msg msg--assistant">
            <div class="msg__bubble">
              <template v-for="(seg, i) in assistantSegments(m)" :key="i">
                <MarkdownView v-if="seg.kind === 'text'" :text="seg.text" class="msg__md" />
                <p v-else class="msg__skip">{{ seg.text }}</p>
              </template>
            </div>
          </div>
        </template>
      </div>

      <div class="takeover-composer">
        <p v-if="conflictMsg" class="takeover-composer__warning" data-test="conflict-warning">{{ conflictMsg }}</p>
        <p v-if="cancelNotice" class="takeover-composer__notice" data-test="cancel-notice">{{ cancelNotice }}</p>
        <div v-if="busy" class="takeover-composer__busy" data-test="busy-hint">
          <span class="takeover-composer__hint">任务执行中，暂不能发送新消息。</span>
          <button
            class="takeover-composer__stop"
            :class="{ 'is-armed': cancelArmed }"
            type="button"
            data-test="stop-btn"
            aria-label="中断任务"
            :disabled="cancelling"
            @click="onCancelClick"
          >{{ cancelLabel }}</button>
        </div>
        <div class="takeover-composer__row">
          <textarea
            v-model="draft"
            class="takeover-composer__input"
            :disabled="busy || sending"
            rows="2"
            placeholder="输入消息，Enter 发送"
            @keydown.enter.exact.prevent="onEnterKey"
          />
          <button
            class="takeover-composer__send"
            type="button"
            :disabled="busy || sending || !draft.trim()"
            @click="onEnterKey"
          >{{ sending ? '发送中…' : '发送' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 对外单一样式：仅浅色，不做深色模式适配 */
.takeover-page {
  min-height: 100vh;
  box-sizing: border-box;
  background: #f5f6f8;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 24px 16px;
}

.takeover-card {
  width: 100%;
  max-width: 860px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.08);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.takeover-card--flat {
  max-width: 480px;
  margin-top: 12vh;
  padding: 32px 24px;
  text-align: center;
}

.takeover-state {
  margin: 0;
  color: #6b7280;
  font-size: 14px;
}

.takeover-error__icon {
  width: 44px;
  height: 44px;
  margin: 0 auto 12px;
  border-radius: 50%;
  background: #fef2f2;
  color: #dc2626;
  font-size: 24px;
  font-weight: 700;
  line-height: 44px;
}

.takeover-error__title {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.takeover-error__hint {
  margin: 0;
  font-size: 13px;
  color: #9ca3af;
}

.takeover-topbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid #e5e7eb;
}

.takeover-topbar__title {
  margin: 0 0 4px;
  font-size: 16px;
  font-weight: 600;
  color: #111827;
  line-height: 1.4;
}

.takeover-topbar__meta {
  margin: 0;
  font-size: 12px;
  color: #6b7280;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.takeover-topbar__seq {
  color: #9ca3af;
}

.takeover-topbar__side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
  flex-shrink: 0;
}

.takeover-topbar__expires {
  font-size: 12px;
  color: #9ca3af;
}

.takeover-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  background: #f3f4f6;
  color: #6b7280;
}

.takeover-badge--pending { background: #f3f4f6; color: #6b7280; }
.takeover-badge--running { background: #fef3c7; color: #b45309; }
.takeover-badge--paused { background: #dbeafe; color: #1d4ed8; }
.takeover-badge--completed { background: #dcfce7; color: #15803d; }
.takeover-badge--failed { background: #fee2e2; color: #b91c1c; }
.takeover-badge--cancelled { background: #f3f4f6; color: #9ca3af; }
.takeover-badge--needs-review { background: #ede9fe; color: #6d28d9; }
.takeover-badge--unknown { background: #f3f4f6; color: #6b7280; }

.takeover-messages {
  flex: 1;
  min-height: 320px;
  max-height: calc(100vh - 260px);
  overflow-y: auto;
  padding: 16px 20px;
  background: #fafbfc;
}

.takeover-messages__empty {
  margin: 48px 0;
  text-align: center;
  color: #9ca3af;
  font-size: 13px;
}

.msg {
  display: flex;
  margin-bottom: 14px;
}

.msg--user {
  justify-content: flex-end;
}

.msg__bubble {
  max-width: 76%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}

.msg--user .msg__bubble {
  background: #2563eb;
  color: #fff;
  border-bottom-right-radius: 2px;
}

.msg--assistant .msg__bubble {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-bottom-left-radius: 2px;
}

.msg__md {
  font-size: 14px;
}

.msg__md + .msg__md,
.msg__skip + .msg__md,
.msg__md + .msg__skip {
  margin-top: 6px;
}

.msg__skip {
  margin: 4px 0 0;
  color: #9ca3af;
  font-size: 12px;
}

.takeover-composer {
  border-top: 1px solid #e5e7eb;
  padding: 12px 20px 16px;
  background: #fff;
}

.takeover-composer__warning {
  margin: 0 0 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #fef3c7;
  color: #92400e;
  font-size: 13px;
}

.takeover-composer__notice {
  margin: 0 0 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #fee2e2;
  color: #b91c1c;
  font-size: 13px;
}

.takeover-composer__busy {
  margin: 0 0 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.takeover-composer__hint {
  font-size: 13px;
  color: #b45309;
}

.takeover-composer__stop {
  padding: 6px 14px;
  border: 1px solid #ef4444;
  border-radius: 8px;
  background: #fff;
  color: #dc2626;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}

.takeover-composer__stop:hover:not(:disabled) {
  background: #fef2f2;
}

.takeover-composer__stop.is-armed {
  background: #dc2626;
  border-color: #dc2626;
  color: #fff;
}

.takeover-composer__stop:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.takeover-composer__row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.takeover-composer__input {
  flex: 1;
  box-sizing: border-box;
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.5;
  font-family: inherit;
  resize: none;
  outline: none;
}

.takeover-composer__input:focus {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.15);
}

.takeover-composer__input:disabled {
  background: #f3f4f6;
  color: #9ca3af;
  cursor: not-allowed;
}

.takeover-composer__send {
  padding: 10px 20px;
  border: none;
  border-radius: 8px;
  background: #2563eb;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  white-space: nowrap;
}

.takeover-composer__send:hover:not(:disabled) {
  background: #1d4ed8;
}

.takeover-composer__send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
