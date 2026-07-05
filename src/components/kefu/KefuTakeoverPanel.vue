<!-- src/components/kefu/KefuTakeoverPanel.vue -->
<template>
  <div class="ktp">
    <div class="ktp__queue">
      <div class="ktp__qbar">
        <el-radio-group v-model="filter" size="small">
          <el-radio-button label="all">全部</el-radio-button>
          <el-radio-button label="needs_human">待人工</el-radio-button>
          <el-radio-button label="takeover">接管中</el-radio-button>
        </el-radio-group>
        <el-button size="small" text @click="loadQueue">🔄</el-button>
      </div>
      <div v-if="!sessions.length" class="ktp__empty">暂无会话</div>
      <div v-else class="ktp__list">
        <div v-for="s in sessions" :key="s.id" class="ktp__item"
             :class="{ 'is-active': s.id === selectedSid }" @click="selectSession(s.id)">
          <span class="ktp__badge" :class="badgeClass(s)">{{ badgeText(s) }}</span>
          <div class="ktp__item-main">
            <div class="ktp__visitor">{{ shortId(s.visitor_id) }}</div>
            <div class="ktp__preview">{{ s.last_message || '（无消息）' }}</div>
          </div>
          <div class="ktp__item-meta">
            <span v-if="s.human_agent_id" class="ktp__agent">{{ s.human_agent_id }}</span>
            <span class="ktp__time">{{ hhmm(s.last_active_at) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="ktp__detail">
      <div v-if="!selectedSid" class="ktp__placeholder">从左侧选择一个会话</div>
      <template v-else>
        <div class="ktp__ops">
          <span class="ktp__ops-info">{{ selected?.human_takeover ? ('接管人：' + (selected?.human_agent_id || '')) : '未接管' }}</span>
          <el-button v-if="!selected?.human_takeover" type="primary" size="small" @click="takeover">接管</el-button>
          <el-button v-else size="small" @click="release">释放</el-button>
        </div>
        <div class="ktp__conv" ref="convEl">
          <div v-for="m in messages" :key="m.id" class="ktp__msg" :class="'ktp__msg--' + m.role">
            <div class="ktp__bubble">
              <span v-if="m.meta && m.meta.author === 'human'" class="ktp__human-tag">人工</span>
              {{ plainText(m.content) }}
            </div>
            <div class="ktp__msg-time">{{ hhmm(m.createdAt) }}</div>
          </div>
        </div>
        <div class="ktp__reply">
          <el-input v-model="replyDraft" :disabled="!selected?.human_takeover"
                    :placeholder="selected?.human_takeover ? '输入人工回复…' : '先接管再回复'"
                    @keydown.enter.prevent="sendReply" />
          <el-button type="primary" :disabled="!selected?.human_takeover || !replyDraft.trim()" @click="sendReply">发送</el-button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import * as api from '@/api/kefu'

const props = defineProps<{ instanceId: string }>()

const sessions = ref<any[]>([])
const filter = ref<'all' | 'needs_human' | 'takeover'>('all')
const selectedSid = ref('')
const messages = ref<any[]>([])
const replyDraft = ref('')
const convEl = ref<HTMLElement | null>(null)
let closeStream: (() => void) | null = null

const selected = computed(() => sessions.value.find(s => s.id === selectedSid.value) || null)

function shortId(v: string) { return (v || '').slice(0, 8) }
function badgeText(s: any) { return s.needs_human ? '待人工' : s.human_takeover ? '接管中' : 'AI' }
function badgeClass(s: any) { return s.needs_human ? 'is-need' : s.human_takeover ? 'is-taken' : 'is-ai' }
function hhmm(ts: string | null) {
  if (!ts) return ''
  const d = new Date(ts); if (isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
function plainText(content: any) {
  const parts = Array.isArray(content) ? content : []
  return parts.filter((p: any) => p && p.type === 'text').map((p: any) => p.text).join('')
}

function queryParams() {
  const p: any = { instance: props.instanceId }
  if (filter.value === 'needs_human') p.needs_human = true
  else if (filter.value === 'takeover') p.takeover = true
  else p.status = 'active'
  return p
}

async function loadQueue() {
  if (!props.instanceId) { sessions.value = []; return }
  sessions.value = (await api.listSessions(queryParams())).sessions
}
async function loadConversation() {
  if (!selectedSid.value) return
  messages.value = (await api.getSessionMessages(selectedSid.value)).messages
  await nextTick(); if (convEl.value) convEl.value.scrollTop = convEl.value.scrollHeight
}
async function selectSession(sid: string) {
  selectedSid.value = sid
  try { await loadConversation() } catch { ElMessage.error('加载对话失败') }
}

function openStream() {
  closeStream?.()
  closeStream = api.createKefuAdminEventStream(props.instanceId, {
    onReady: () => { loadQueue().catch(() => {}); if (selectedSid.value) loadConversation().catch(() => {}) },
    onEvent: (e) => { loadQueue().catch(() => {}); if (e.sid && e.sid === selectedSid.value) loadConversation().catch(() => {}) },
  })
}

async function takeover() {
  if (!selectedSid.value) return
  try { await api.takeoverSession(selectedSid.value); await loadQueue(); await loadConversation() }
  catch (e: any) { ElMessage.error(e?.response?.data?.error || '接管失败') }
}
async function release() {
  if (!selectedSid.value) return
  try { await api.releaseSession(selectedSid.value); await loadQueue(); await loadConversation() }
  catch (e: any) { ElMessage.error(e?.response?.data?.error || '释放失败') }
}
async function sendReply() {
  const text = replyDraft.value.trim()
  if (!text || !selected.value?.human_takeover) return
  try { await api.humanReply(selectedSid.value, text); replyDraft.value = ''; await loadConversation() }
  catch (e: any) { ElMessage.error(e?.response?.data?.error || '发送失败') }
}

watch(filter, () => { loadQueue() })
watch(() => props.instanceId, () => { selectedSid.value = ''; messages.value = []; loadQueue().catch(() => {}); openStream() })

onMounted(() => {
  loadQueue().catch(() => {})
  openStream()
})
onBeforeUnmount(() => { closeStream?.() })

defineExpose({ sessions, filter, selectedSid, messages, replyDraft, selected, selectSession, takeover, release, sendReply, loadQueue })
</script>

<style scoped>
.ktp { display: flex; height: 520px; border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 8px; overflow: hidden; }
.ktp__queue { width: 300px; border-right: 1px solid var(--el-border-color-lighter, #ebeef5); display: flex; flex-direction: column; }
.ktp__qbar { display: flex; align-items: center; justify-content: space-between; padding: 8px; border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5); }
.ktp__empty, .ktp__placeholder { padding: 40px 12px; text-align: center; color: var(--el-text-color-secondary, #909399); }
.ktp__list { flex: 1; overflow-y: auto; }
.ktp__item { display: flex; gap: 8px; padding: 10px; border-bottom: 1px solid var(--el-border-color-lighter, #f2f3f5); cursor: pointer; }
.ktp__item:hover { background: var(--el-fill-color-light, #f5f7fa); }
.ktp__item.is-active { background: var(--el-color-primary-light-9, #ecf5ff); }
.ktp__badge { flex-shrink: 0; font-size: 11px; padding: 1px 6px; border-radius: 8px; height: fit-content; }
.ktp__badge.is-need { background: #fff3e0; color: #e6913a; }
.ktp__badge.is-taken { background: #eef1fe; color: #4f6ef2; }
.ktp__badge.is-ai { background: var(--el-fill-color, #f0f2f5); color: var(--el-text-color-secondary, #909399); }
.ktp__item-main { flex: 1; min-width: 0; }
.ktp__visitor { font-size: 12px; color: var(--el-text-color-regular, #606266); }
.ktp__preview { font-size: 12px; color: var(--el-text-color-secondary, #909399); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ktp__item-meta { flex-shrink: 0; text-align: right; font-size: 11px; color: var(--el-text-color-secondary, #909399); }
.ktp__detail { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.ktp__ops { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5); }
.ktp__ops-info { font-size: 13px; color: var(--el-text-color-regular, #606266); }
.ktp__conv { flex: 1; overflow-y: auto; padding: 12px; }
.ktp__msg { display: flex; flex-direction: column; margin-bottom: 12px; }
.ktp__msg--user { align-items: flex-end; }
.ktp__bubble { max-width: 76%; padding: 8px 12px; border-radius: 10px; line-height: 1.5; word-break: break-word; }
.ktp__msg--assistant .ktp__bubble { background: var(--el-fill-color-light, #f4f4f5); color: var(--el-text-color-primary, #303133); }
.ktp__msg--user .ktp__bubble { background: var(--el-color-primary, #4f6ef2); color: #fff; }
.ktp__human-tag { display: inline-block; font-size: 10px; margin-right: 4px; padding: 0 4px; border-radius: 6px; background: #eef1fe; color: #4f6ef2; }
.ktp__msg-time { font-size: 11px; color: var(--el-text-color-secondary, #909399); margin-top: 2px; }
.ktp__reply { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--el-border-color-lighter, #ebeef5); }
</style>
