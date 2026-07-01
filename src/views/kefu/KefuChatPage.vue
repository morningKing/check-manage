<!-- src/views/kefu/KefuChatPage.vue -->
<template>
  <div class="kefu-page">
    <header class="kefu-header">
      <span class="title">{{ config?.name || '在线客服' }}</span>
      <el-button size="small" @click="drawer = true">🗂 自助服务</el-button>
    </header>
    <main class="kefu-messages" ref="scroller">
      <div v-if="config?.welcome_message" class="msg assistant"><MarkdownView :text="config.welcome_message" /></div>
      <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
        <MarkdownView v-if="m.role==='assistant'" :text="plainText(m.content)" />
        <span v-else class="user-text">{{ plainText(m.content) }}</span>
      </div>
      <div v-if="sending" class="typing">正在输入…</div>
    </main>
    <footer class="kefu-input">
      <el-input v-model="draft" type="textarea" :rows="2" placeholder="输入你的问题…" @keydown.enter.prevent="send" />
      <el-button type="primary" :disabled="!draft.trim() || sending" @click="send">发送</el-button>
    </footer>
    <el-drawer v-model="drawer" title="自助服务" direction="rtl" size="360px">
      <KefuSelfServicePanel :items="faq" @click="onFaqClick" @escalate="onEscalate" />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import KefuSelfServicePanel from '@/components/kefu/KefuSelfServicePanel.vue'
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
const scroller = ref<HTMLElement | null>(null)
let closeStream: (() => void) | null = null

function normalize(content: any) { return Array.isArray(content) ? content : [{ type: 'text', text: String(content ?? '') }] }
function plainText(content: any) { return normalize(content).filter((p: any) => p.type === 'text').map((p: any) => p.text).join('') }
async function scrollDown() { await nextTick(); if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight }

async function reload() { messages.value = (await api.getKefuHistory(sessionId.value)).messages; await scrollDown() }

async function send() {
  const text = draft.value.trim(); if (!text || sending.value) return
  draft.value = ''; sending.value = true
  messages.value.push({ id: 'local_' + Date.now(), role: 'user', content: [{ type: 'text', text }], createdAt: null })
  await scrollDown()
  try { await api.sendKefuMessage(sessionId.value, text) } catch { sending.value = false }
}

function onFaqClick(id: string) { api.clickKefuFaq(props.slug, id) }
async function onEscalate(question: string) { drawer.value = false; draft.value = question; await send() }

onMounted(async () => {
  config.value = await api.getKefuConfig(props.slug)
  const s = await api.createKefuSession(props.slug); sessionId.value = s.id
  faq.value = (await api.getKefuFaq(props.slug)).items
  await reload()
  closeStream = api.createKefuEventStream(sessionId.value, {
    onIdle: async () => { await reload(); sending.value = false },
    onError: () => {},
  })
})
onBeforeUnmount(() => { closeStream?.() })
defineExpose({ sessionId, onEscalate, messages, sending })
</script>
