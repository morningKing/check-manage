<!-- src/views/kefu/KefuChatPage.vue -->
<template>
  <div class="kefu-page">
    <div class="kefu-card" :class="{ 'is-plain': loading || loadError }">
      <div v-if="loading" class="kefu-skeleton">
        <div class="sk-header"><div class="sk-avatar" /><div class="sk-lines"><i /><i /></div></div>
        <div class="sk-body"><span /><span /><span /></div>
      </div>
      <div v-else-if="loadError" class="kefu-error">
        <div class="kefu-error__icon">💬</div>
        <p>客服暂不可用，请稍后再试</p>
      </div>
      <template v-else>
        <div class="kefu-main">
          <header class="kefu-header">
            <div class="kefu-header__id">
              <div class="kefu-avatar" :style="{ background: headerAvatarColor }">
                <img v-if="config?.branding?.logo" :src="config.branding.logo" alt="" />
                <span v-else>{{ headerAvatarInitial }}</span>
              </div>
              <div class="kefu-header__meta">
                <span class="kefu-header__name">{{ config?.name || '在线客服' }}</span>
                <span class="kefu-header__status"><i class="dot" /> {{ humanMode ? '人工客服接入中' : '在线' }}</span>
              </div>
            </div>
            <div class="kefu-header__actions">
              <el-button v-if="!humanMode" size="small" @click="requestHuman">🙋 转人工</el-button>
              <el-button v-if="hasBlocks" class="svc-toggle" size="small" @click="drawer = true">🗂 自助服务</el-button>
            </div>
          </header>
          <main class="kefu-messages" ref="scroller">
            <div v-if="humanMode" class="kefu-human-banner">已为你转接人工客服，请稍候…</div>
            <div v-if="showWelcome" class="kefu-welcome">
              <KefuMessageBubble
                v-if="config?.welcome_message"
                :message="{ id: 'welcome', role: 'assistant', content: [{ type: 'text', text: config.welcome_message }], createdAt: null }"
                :agent-name="config?.name || '在线客服'"
                :agent-logo="config?.branding?.logo" />
              <div v-if="bubbles.length" class="bubbles">
                <button v-for="(b, i) in bubbles" :key="i" class="bubble" @click="askBubble(b)">{{ b }}</button>
              </div>
            </div>
            <KefuMessageBubble
              v-for="m in messages" :key="m.id"
              :message="m"
              :agent-name="config?.name || '在线客服'"
              :agent-logo="config?.branding?.logo" />
            <div v-if="sending" class="typing-row" role="status" aria-label="正在输入">
              <span class="typing-bubble"><i></i><i></i><i></i></span>
            </div>
          </main>
          <KefuComposer
            :draft="draft" :pending="pending" :sending="sending"
            @update:draft="draft = $event"
            @pick-files="onPickFiles"
            @remove-pending="removePending"
            @send="send" />
        </div>
        <aside v-if="hasBlocks" class="kefu-column">
          <KefuServiceColumn :blocks="blocks" :faqItems="faq" @faqClick="onFaqClick" @escalate="onEscalate" />
        </aside>
        <el-drawer v-if="hasBlocks" v-model="drawer" title="自助服务" direction="rtl" size="360px">
          <KefuServiceColumn :blocks="blocks" :faqItems="faq" @faqClick="onFaqClick" @escalate="onEscalateDrawer" />
        </el-drawer>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import KefuMessageBubble from '@/components/kefu/KefuMessageBubble.vue'
import KefuServiceColumn from '@/components/kefu/KefuServiceColumn.vue'
import KefuComposer from '@/components/kefu/KefuComposer.vue'
import { avatarInitial, avatarColor } from '@/components/kefu/avatar'
import * as api from '@/api/kefuPublic'
import type { KefuConfig, KefuFaqItem, KefuMessage } from '@/api/kefuPublic'

const props = defineProps<{ slug: string }>()
const config = ref<KefuConfig | null>(null)
const sessionId = ref('')
const faq = ref<KefuFaqItem[]>([])
const messages = ref<KefuMessage[]>([])
const draft = ref('')
const sending = ref(false)
const drawer = ref(false)
const loadError = ref(false)
const scroller = ref<HTMLElement | null>(null)
const pending = ref<{ name: string; path: string }[]>([])
let closeStream: (() => void) | null = null
const humanMode = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

const loading = ref(true)
const headerAvatarInitial = computed(() => avatarInitial(config.value?.name))
const headerAvatarColor = computed(() => avatarColor(config.value?.name))

const bubbles = computed(() => config.value?.guided_questions || [])
const blocks = computed(() => config.value?.panel_blocks || [])
const hasBlocks = computed(() => blocks.value.some(b => b.enabled !== false))
const showWelcome = computed(() => messages.value.length === 0)

async function scrollDown() { await nextTick(); if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight }

async function reload() { messages.value = (await api.getKefuHistory(sessionId.value)).messages; await scrollDown() }

async function onPickFiles(files: File[]) {
  for (const f of files) {
    try {
      const r = await api.uploadKefuFile(sessionId.value, f)
      pending.value.push({ name: r.name, path: r.path })
    } catch (err: any) { ElMessage.error(err?.message || '上传失败') }
  }
}

function removePending(i: number) { pending.value.splice(i, 1) }

function startPolling() { if (!pollTimer) pollTimer = setInterval(() => { reload().catch(() => {}) }, 3000) }
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }
function enterHumanMode() {
  if (humanMode.value) return
  humanMode.value = true
  sending.value = false
  startPolling()
}
async function requestHuman() {
  if (humanMode.value) return
  try { await api.requestHuman(sessionId.value); enterHumanMode() }
  catch { ElMessage.error('转人工失败，请稍后重试') }
}

async function send() {
  const text = draft.value.trim()
  const atts = pending.value.map(p => p.path)
  if ((!text && atts.length === 0) || sending.value) return
  const savedPending = pending.value.slice()  // for restore-on-failure
  draft.value = ''; sending.value = true
  const parts: any[] = []
  if (text) parts.push({ type: 'text', text })
  for (const p of pending.value) parts.push({ type: 'file', name: p.name, path: p.path })
  const localMsgId = 'local_' + Date.now()
  messages.value.push({ id: localMsgId, role: 'user', content: parts, createdAt: null })
  pending.value = []
  await scrollDown()
  try {
    const resp = await api.sendKefuMessage(sessionId.value, text, atts)
    if (resp?.humanTakeover || humanMode.value) { sending.value = false; enterHumanMode() }
  }
  catch {
    ElMessage.error('发送失败，请稍后重试')
    sending.value = false
    draft.value = text           // restore text
    pending.value = savedPending // restore attachments
    // remove the optimistic bubble
    messages.value = messages.value.filter(m => m.id !== localMsgId)
  }
}

function onFaqClick(id: string) { api.clickKefuFaq(props.slug, id) }
async function onEscalate(question: string) { if (sending.value) return; drawer.value = false; draft.value = question; await send() }
async function onEscalateDrawer(question: string) { drawer.value = false; await onEscalate(question) }
async function askBubble(q: string) { if (sending.value) return; draft.value = q; await send() }

onMounted(async () => {
  try {
    config.value = await api.getKefuConfig(props.slug)
    const s = await api.createKefuSession(props.slug); sessionId.value = s.id
    faq.value = (await api.getKefuFaq(props.slug)).items
    await reload()
    closeStream = api.createKefuEventStream(sessionId.value, {
      onIdle: async () => { await reload(); sending.value = false },
      onError: () => { sending.value = false; ElMessage.error('客服暂时无法回复，请稍后重试') },
    })
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
    if (!loadError.value) await scrollDown()
  }
})
onBeforeUnmount(() => { closeStream?.(); stopPolling() })
defineExpose({ sessionId, onEscalate, messages, sending, askBubble, blocks, bubbles, pending, onPickFiles, send, draft, requestHuman, humanMode })
</script>

<style scoped>
.kefu-page {
  /* brand accent — SCOPED to this page only; does NOT touch global --el-color-primary */
  --kefu-accent: #4f6ef2;
  --kefu-accent-hover: #3f5fe0;
  --kefu-accent-soft: #eef1fe;
  --kefu-accent-contrast: #ffffff;
  height: 100vh; display: flex; align-items: center; justify-content: center;
  background: var(--el-bg-color-page, #f5f7fa); padding: 24px; box-sizing: border-box;
}
.kefu-card {
  display: flex; width: min(1080px, 94vw); height: min(880px, 92vh);
  background: var(--el-bg-color, #fff); border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 16px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.08); overflow: hidden;
}
.kefu-card.is-plain { align-items: center; justify-content: center; }
.kefu-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }

/* header */
.kefu-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px; border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.kefu-header__id { display: flex; align-items: center; gap: 10px; }
.kefu-avatar {
  width: 40px; height: 40px; border-radius: 50%; overflow: hidden; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 16px; font-weight: 600;
}
.kefu-avatar img { width: 100%; height: 100%; object-fit: cover; }
.kefu-header__meta { display: flex; flex-direction: column; line-height: 1.3; }
.kefu-header__name { font-weight: 600; color: var(--el-text-color-primary, #303133); }
.kefu-header__status { font-size: 12px; color: var(--el-text-color-secondary, #909399); display: inline-flex; align-items: center; gap: 4px; }
.kefu-header__status .dot { width: 7px; height: 7px; border-radius: 50%; background: #22c55e; display: inline-block; }
.kefu-header__actions { display: flex; align-items: center; gap: 8px; }
.kefu-human-banner {
  margin: 0 0 12px; padding: 8px 12px; border-radius: 8px;
  background: var(--kefu-accent-soft, #eef1fe); color: var(--kefu-accent, #4f6ef2);
  font-size: 13px; text-align: center;
}

/* messages */
.kefu-messages { flex: 1; overflow-y: auto; padding: 20px 24px; }
.kefu-messages::-webkit-scrollbar { width: 6px; }
.kefu-messages::-webkit-scrollbar-thumb { background: var(--el-border-color, #dcdfe6); border-radius: 3px; }
.kefu-welcome { margin-bottom: 16px; }
.kefu-welcome .bubbles { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.bubble {
  border: 1px solid var(--kefu-accent-soft, #eef1fe); background: var(--kefu-accent-soft, #eef1fe);
  color: var(--kefu-accent, #4f6ef2); border-radius: 16px; padding: 6px 14px; cursor: pointer;
  font-size: 13px; transition: background .15s ease;
}
.bubble:hover { background: #e3e8fd; }

/* typing */
.typing-row { display: flex; margin-bottom: 16px; }
.typing-bubble {
  display: inline-flex; gap: 4px; padding: 10px 12px; border-radius: 12px;
  border-top-left-radius: 4px; background: var(--el-fill-color-light, #f4f4f5);
}
.typing-bubble i {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--el-text-color-secondary, #909399);
  animation: kmb-blink 1.2s infinite ease-in-out both;
}
.typing-bubble i:nth-child(2) { animation-delay: 0.2s; }
.typing-bubble i:nth-child(3) { animation-delay: 0.4s; }
@keyframes kmb-blink { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }

/* right column */
.kefu-column { width: 320px; border-left: 1px solid var(--el-border-color-lighter, #ebeef5); overflow-y: auto; padding: 16px; background: var(--el-bg-color-page, #f9fafb); }

/* skeleton + error */
.kefu-skeleton { width: 100%; max-width: 420px; padding: 24px; }
.kefu-skeleton .sk-header { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.kefu-skeleton .sk-avatar { width: 40px; height: 40px; border-radius: 50%; background: var(--el-fill-color, #f0f2f5); }
.kefu-skeleton .sk-lines { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.kefu-skeleton .sk-lines i { height: 10px; border-radius: 5px; background: var(--el-fill-color, #f0f2f5); }
.kefu-skeleton .sk-lines i:first-child { width: 40%; }
.kefu-skeleton .sk-lines i:last-child { width: 24%; }
.kefu-skeleton .sk-body { display: flex; flex-direction: column; gap: 12px; }
.kefu-skeleton .sk-body span { height: 44px; border-radius: 12px; background: var(--el-fill-color-light, #f5f7fa); }
.kefu-skeleton .sk-body span:nth-child(1) { width: 70%; }
.kefu-skeleton .sk-body span:nth-child(2) { width: 55%; align-self: flex-end; background: var(--kefu-accent-soft, #eef1fe); }
.kefu-skeleton .sk-body span:nth-child(3) { width: 60%; }
.kefu-error { text-align: center; color: var(--el-text-color-secondary, #909399); }
.kefu-error__icon { font-size: 40px; margin-bottom: 8px; }

/* responsive: full-bleed card + drawer under 992px */
.svc-toggle { display: none; }
@media (max-width: 991px) {
  .kefu-page { padding: 0; }
  .kefu-card { width: 100vw; height: 100vh; border-radius: 0; border: none; box-shadow: none; }
  .kefu-column { display: none; }
  .svc-toggle { display: inline-flex; }
}
</style>
