<!-- src/views/kefu/KefuChatPage.vue -->
<template>
  <div class="kefu-page" :class="{ 'with-column': hasBlocks }">
    <template v-if="!loadError">
      <div class="kefu-main">
        <header class="kefu-header">
          <span class="title">{{ config?.name || '在线客服' }}</span>
          <el-button v-if="hasBlocks" class="svc-toggle" size="small" @click="drawer = true">🗂 自助服务</el-button>
        </header>
        <main class="kefu-messages" ref="scroller">
          <div v-if="showWelcome" class="kefu-welcome">
            <KefuMessageBubble
              v-if="config?.welcome_message"
              :message="{ id: 'welcome', role: 'assistant', content: [{ type: 'text', text: config.welcome_message }], createdAt: null }"
              :agent-name="config?.name || '在线客服'"
              :agent-logo="config?.branding?.logo" />
            <div v-if="bubbles.length" class="bubbles">
              <button v-for="(b,i) in bubbles" :key="i" class="bubble" @click="askBubble(b)">{{ b }}</button>
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
        <footer class="kefu-input">
          <div v-if="pending.length" class="pending">
            <span v-for="(p,i) in pending" :key="i" class="pending-chip">📎 {{ p.name }} <b @click="removePending(i)">✕</b></span>
          </div>
          <div class="input-row">
            <button class="attach-btn" type="button" title="上传文件" @click="fileInput?.click()">📎</button>
            <input ref="fileInput" type="file" multiple class="hidden-file" @change="onFileChange" />
            <el-input v-model="draft" type="textarea" :rows="2" placeholder="输入你的问题…" @keydown.enter="onEnter" />
            <el-button type="primary" :disabled="(!draft.trim() && pending.length===0) || sending" @click="send">发送</el-button>
          </div>
        </footer>
      </div>
      <aside v-if="hasBlocks" class="kefu-column">
        <KefuServiceColumn :blocks="blocks" :faqItems="faq" @faqClick="onFaqClick" @escalate="onEscalate" />
      </aside>
      <el-drawer v-if="hasBlocks" v-model="drawer" title="自助服务" direction="rtl" size="360px">
        <KefuServiceColumn :blocks="blocks" :faqItems="faq" @faqClick="onFaqClick" @escalate="onEscalateDrawer" />
      </el-drawer>
    </template>
    <div v-else class="kefu-error">客服暂不可用，请稍后再试</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import KefuMessageBubble from '@/components/kefu/KefuMessageBubble.vue'
import KefuServiceColumn from '@/components/kefu/KefuServiceColumn.vue'
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
const fileInput = ref<HTMLInputElement | null>(null)
let closeStream: (() => void) | null = null

const bubbles = computed(() => config.value?.guided_questions || [])
const blocks = computed(() => config.value?.panel_blocks || [])
const hasBlocks = computed(() => blocks.value.some(b => b.enabled !== false))
const showWelcome = computed(() => messages.value.length === 0)

async function scrollDown() { await nextTick(); if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight }

async function reload() { messages.value = (await api.getKefuHistory(sessionId.value)).messages; await scrollDown() }

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) await onPickFiles(Array.from(input.files))
  input.value = ''
}

async function onPickFiles(files: File[]) {
  for (const f of files) {
    try {
      const r = await api.uploadKefuFile(sessionId.value, f)
      pending.value.push({ name: r.name, path: r.path })
    } catch (err: any) { ElMessage.error(err?.message || '上传失败') }
  }
}

function removePending(i: number) { pending.value.splice(i, 1) }

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
  try { await api.sendKefuMessage(sessionId.value, text, atts) }
  catch {
    ElMessage.error('发送失败，请稍后重试')
    sending.value = false
    draft.value = text           // restore text
    pending.value = savedPending // restore attachments
    // remove the optimistic bubble
    messages.value = messages.value.filter(m => m.id !== localMsgId)
  }
}

function onEnter(e: KeyboardEvent) {
  if (e.isComposing) return          // IME candidate selection — not a submit
  if (e.shiftKey) return             // Shift+Enter = newline
  e.preventDefault()
  send()
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
  }
})
onBeforeUnmount(() => { closeStream?.() })
defineExpose({ sessionId, onEscalate, messages, sending, askBubble, blocks, bubbles, pending, onPickFiles, send, draft })
</script>

<style scoped>
.kefu-page { display: flex; height: 100vh; }
.kefu-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.kefu-column { width: 340px; border-left: 1px solid var(--el-border-color, #eee); overflow-y: auto; padding: 12px; }
.kefu-welcome .bubbles { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.bubble { border: 1px solid var(--el-color-primary, #409eff); color: var(--el-color-primary, #409eff); background: transparent; border-radius: 16px; padding: 6px 14px; cursor: pointer; }
.svc-toggle { display: none; }
@media (max-width: 991px) {
  .kefu-column { display: none; }
  .svc-toggle { display: inline-flex; }
}
@media (min-width: 992px) {
  .svc-toggle { display: none; }  /* desktop uses persistent column, not drawer */
}
.hidden-file { display: none; }
.pending { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px 8px; }
.pending-chip { display: inline-flex; align-items: center; gap: 4px; background: var(--el-color-primary-light-9, #ecf5ff); border: 1px solid var(--el-color-primary-light-7, #c6e2ff); border-radius: 12px; padding: 2px 8px; font-size: 12px; }
.pending-chip b { cursor: pointer; font-weight: normal; color: var(--el-text-color-secondary, #909399); }
.pending-chip b:hover { color: var(--el-color-danger, #f56c6c); }
.input-row { display: flex; align-items: flex-end; gap: 6px; padding: 8px; }
.attach-btn { background: none; border: 1px solid var(--el-border-color, #dcdfe6); border-radius: 6px; padding: 6px 8px; cursor: pointer; font-size: 16px; line-height: 1; flex-shrink: 0; }
.attach-btn:hover { background: var(--el-fill-color-light, #f5f7fa); }
.kefu-welcome { margin-bottom: 16px; }
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
</style>
