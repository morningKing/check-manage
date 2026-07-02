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
            <MarkdownView v-if="config?.welcome_message" :text="config.welcome_message" />
            <div v-if="bubbles.length" class="bubbles">
              <button v-for="(b,i) in bubbles" :key="i" class="bubble" @click="askBubble(b)">{{ b }}</button>
            </div>
          </div>
          <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
            <MarkdownView v-if="m.role==='assistant'" :text="plainText(m.content)" />
            <span v-else class="user-text">{{ plainText(m.content) }}</span>
          </div>
          <div v-if="sending" class="typing">正在输入…</div>
        </main>
        <footer class="kefu-input">
          <el-input v-model="draft" type="textarea" :rows="2" placeholder="输入你的问题…" @keydown.enter="onEnter" />
          <el-button type="primary" :disabled="!draft.trim() || sending" @click="send">发送</el-button>
        </footer>
      </div>
      <aside v-if="hasBlocks" class="kefu-column">
        <KefuServiceColumn :blocks="blocks" :faqItems="faq" @faqClick="onFaqClick" @escalate="onEscalate" />
      </aside>
      <el-drawer v-model="drawer" title="自助服务" direction="rtl" size="360px">
        <KefuServiceColumn :blocks="blocks" :faqItems="faq" @faqClick="onFaqClick" @escalate="onEscalateDrawer" />
      </el-drawer>
    </template>
    <div v-else class="kefu-error">客服暂不可用，请稍后再试</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
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
let closeStream: (() => void) | null = null

const bubbles = computed(() => config.value?.guided_questions || [])
const blocks = computed(() => config.value?.panel_blocks || [])
const hasBlocks = computed(() => blocks.value.some(b => b.enabled !== false))
const showWelcome = computed(() => messages.value.length === 0)

function normalize(content: any) { return Array.isArray(content) ? content : [{ type: 'text', text: String(content ?? '') }] }
function plainText(content: any) { return normalize(content).filter((p: any) => p.type === 'text').map((p: any) => p.text).join('') }
async function scrollDown() { await nextTick(); if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight }

async function reload() { messages.value = (await api.getKefuHistory(sessionId.value)).messages; await scrollDown() }

async function send() {
  const text = draft.value.trim(); if (!text || sending.value) return
  draft.value = ''; sending.value = true
  messages.value.push({ id: 'local_' + Date.now(), role: 'user', content: [{ type: 'text', text }], createdAt: null })
  await scrollDown()
  try { await api.sendKefuMessage(sessionId.value, text) } catch { ElMessage.error('发送失败，请稍后重试'); sending.value = false }
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
defineExpose({ sessionId, onEscalate, messages, sending, askBubble, blocks, bubbles })
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
</style>
