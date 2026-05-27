<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import {
  ElButton, ElInput, ElScrollbar, ElIcon, ElTooltip, ElEmpty, ElMessageBox, ElMessage,
  ElDrawer,
} from 'element-plus'
import {
  Plus, Promotion, Delete, EditPen, Paperclip, Close, Document, Loading,
  CopyDocument, Download,
} from '@element-plus/icons-vue'
import { Bubble, Thinking } from 'vue-element-plus-x'
import 'vue-element-plus-x/styles/index.css'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import ToolCallBubble from '@/components/ai-chat/ToolCallBubble.vue'
import ArtifactCard from '@/components/ai-chat/ArtifactCard.vue'
import { splitArtifacts, isMarkdownLang, artifactFilename, downloadText, type CodeSegment } from '@/utils/artifacts'
import { useAiChatStore } from '@/stores/aiChat'
import type { AiMessage } from '@/api/aiChat'

const store = useAiChatStore()

const input = ref('')
const fileInputEl = ref<HTMLInputElement | null>(null)
const scroller = ref<InstanceType<typeof ElScrollbar> | null>(null)

const sessions = computed(() => store.sessions)
const activeId = computed(() => store.activeSessionId)
const messages = computed(() => store.activeMessages)
const streaming = computed(() => store.isStreaming)
const attachments = computed(() => store.activeAttachments)
const reasoning = computed(() => (activeId.value ? store.reasoning[activeId.value] || '' : ''))
const thinking = computed(() => (activeId.value ? !!store.thinking[activeId.value] : false))

const canSend = computed(() => !streaming.value && (input.value.trim() || attachments.value.length))

function hasText(m: AiMessage): boolean {
  return m.content.some(p => p.type === 'text' && p.text)
}

// ---- Artifacts (Claude-style file preview) ----
const previewOpen = ref(false)
const preview = ref<{ lang: string; code: string; filename: string } | null>(null)
const previewMarkdown = computed(() => {
  if (!preview.value) return ''
  const { lang, code } = preview.value
  return isMarkdownLang(lang) ? code : '```' + (lang || '') + '\n' + code + '\n```'
})
function openPreview(seg: CodeSegment, idx: number) {
  preview.value = { lang: seg.lang, code: seg.code, filename: artifactFilename(seg.lang, idx) }
  previewOpen.value = true
}
async function copyPreview() {
  if (!preview.value) return
  try { await navigator.clipboard.writeText(preview.value.code); ElMessage.success('已复制') }
  catch { ElMessage.error('复制失败') }
}
function downloadPreview() {
  if (preview.value) downloadText(preview.value.filename, preview.value.code)
}

async function scrollToBottom() {
  await nextTick()
  scroller.value?.setScrollTop(9_999_999)
}
watch(() => messages.value.map(m => m.content.map(c => (c as any).text || '').join()).join('|'), scrollToBottom)
watch(reasoning, scrollToBottom)

onMounted(async () => {
  try {
    await store.loadSessions()
    if (sessions.value.length) await store.openSession(sessions.value[0].id)
  } catch { /* surfaced by interceptor */ }
})

async function newSession() {
  try { await store.startNewSession() } catch { ElMessage.error('创建会话失败') }
}
async function selectSession(id: string) {
  if (id !== activeId.value) await store.openSession(id)
}
async function renameSession(id: string, current: string) {
  try {
    const res = await ElMessageBox.prompt('重命名会话', '重命名', { inputValue: current })
    const value = (res as { value?: string }).value
    if (value?.trim()) await store.renameSession(id, value.trim())
  } catch { /* cancelled */ }
}
async function removeSession(id: string) {
  try {
    await ElMessageBox.confirm('删除该会话？', '删除', { type: 'warning' })
    await store.closeSession(id)
  } catch { /* cancelled */ }
}

function pickFiles() { fileInputEl.value?.click() }
async function onFilesPicked(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (!files) return
  if (!activeId.value) await newSession()
  for (const f of Array.from(files)) {
    try { await store.uploadAttachment(f) } catch { ElMessage.error(`上传失败：${f.name}`) }
  }
  ;(e.target as HTMLInputElement).value = ''
}

async function send() {
  if (!canSend.value) return
  const text = input.value.trim()
  input.value = ''
  if (!activeId.value) await newSession()
  try { await store.sendUserMessage(text) } catch { ElMessage.error('发送失败') }
}
function onKey(e: Event) {
  const ev = e as KeyboardEvent
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); send() }
}
</script>

<template>
  <div class="ai-chat">
    <!-- 会话侧栏 -->
    <aside class="ai-chat__sidebar">
      <ElButton class="ai-chat__new" type="primary" :icon="Plus" @click="newSession">新建会话</ElButton>
      <ElScrollbar class="ai-chat__sessions">
        <div
          v-for="s in sessions" :key="s.id"
          class="session-item" :class="{ active: s.id === activeId }"
          @click="selectSession(s.id)"
        >
          <span class="session-item__title">{{ s.title || '新会话' }}</span>
          <span class="session-item__actions" @click.stop>
            <ElIcon @click="renameSession(s.id, s.title)"><EditPen /></ElIcon>
            <ElIcon @click="removeSession(s.id)"><Delete /></ElIcon>
          </span>
        </div>
        <ElEmpty v-if="!sessions.length" description="暂无会话" :image-size="60" />
      </ElScrollbar>
    </aside>

    <!-- 对话主区 -->
    <section class="ai-chat__main">
      <ElScrollbar ref="scroller" class="ai-chat__messages">
        <div v-if="!activeId" class="ai-chat__welcome">
          <ElEmpty description="开启一个会话，向 AI 助手提问或上传文件">
            <ElButton type="primary" :icon="Plus" @click="newSession">开启新会话</ElButton>
          </ElEmpty>
        </div>

        <template v-else>
          <div class="ai-thread">
            <div
              v-for="m in messages" :key="m.id"
              class="msg" :class="`msg--${m.role}`"
            >
              <div class="msg__role" v-if="m.role !== 'user'">AI 助手</div>
              <Bubble
                :placement="m.role === 'user' ? 'end' : 'start'"
                :variant="m.role === 'user' ? 'filled' : 'borderless'"
                :class="['ai-bubble', 'ai-bubble--' + m.role]"
              >
                <template #content>
                  <template v-for="(p, i) in m.content" :key="i">
                    <div v-if="p.type === 'file'" class="file-chip">
                      <ElIcon><Document /></ElIcon><span>{{ p.name }}</span>
                    </div>
                    <ToolCallBubble
                      v-else-if="p.type === 'tool_use'"
                      :name="p.name" :title="p.title" :status="p.status"
                      :input="p.input" :result="p.result"
                    />
                    <template v-else-if="p.type === 'text' && p.text">
                      <!-- assistant: lift big code/doc blocks into artifact cards -->
                      <template v-if="m.role === 'assistant'">
                        <template v-for="(seg, si) in splitArtifacts(p.text)" :key="si">
                          <MarkdownView v-if="seg.type === 'text' && seg.text.trim()" :text="seg.text" />
                          <ArtifactCard
                            v-else-if="seg.type === 'code'"
                            :lang="seg.lang" :code="seg.code" :index="si"
                            @preview="openPreview(seg, si)"
                          />
                        </template>
                      </template>
                      <MarkdownView v-else :text="p.text" />
                    </template>
                  </template>
                </template>
              </Bubble>
            </div>

            <!-- 思考过程 -->
            <Thinking
              v-if="reasoning"
              class="ai-thinking"
              :content="reasoning"
              :status="thinking ? 'thinking' : 'end'"
            />

            <div v-if="streaming && !messages.some(m => m.role === 'assistant' && hasText(m)) && !reasoning" class="ai-chat__pending">
              <ElIcon class="spin"><Loading /></ElIcon> 正在思考…
            </div>
          </div>
        </template>
      </ElScrollbar>

      <!-- 输入区 -->
      <div class="ai-chat__composer">
        <div v-if="attachments.length" class="composer-attachments">
          <span v-for="a in attachments" :key="a.path" class="attach-chip">
            <ElIcon><Document /></ElIcon>{{ a.name }}
            <ElIcon class="attach-chip__x" @click="store.removeAttachment(a.path)"><Close /></ElIcon>
          </span>
        </div>
        <ElInput
          v-model="input" type="textarea" :rows="3" resize="none"
          placeholder="给 AI 助手发消息（Enter 发送，Shift+Enter 换行）"
          @keydown="onKey"
        />
        <div class="composer-bar">
          <input ref="fileInputEl" type="file" multiple hidden @change="onFilesPicked" />
          <ElTooltip content="上传文件">
            <ElButton :icon="Paperclip" circle :loading="store.uploading" @click="pickFiles" />
          </ElTooltip>
          <ElButton type="primary" :icon="Promotion" :disabled="!canSend" @click="send">发送</ElButton>
        </div>
      </div>
    </section>

    <!-- 制品预览面板（Claude 风格） -->
    <ElDrawer v-model="previewOpen" :title="preview?.filename || '预览'" direction="rtl" size="52%">
      <template #header>
        <div class="preview-head">
          <span class="preview-head__name">{{ preview?.filename }}</span>
          <span class="preview-head__actions">
            <ElButton size="small" :icon="CopyDocument" @click="copyPreview">复制</ElButton>
            <ElButton size="small" type="primary" :icon="Download" @click="downloadPreview">下载</ElButton>
          </span>
        </div>
      </template>
      <div class="preview-body">
        <MarkdownView :text="previewMarkdown" />
      </div>
    </ElDrawer>
  </div>
</template>

<style scoped lang="scss">
.ai-chat {
  display: flex;
  height: 100%;
  min-height: 0;
  background: var(--el-bg-color);
  border-radius: 8px;
  overflow: hidden;
}
.ai-chat__sidebar {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--el-border-color-light);
  padding: 12px;
  gap: 10px;
}
.ai-chat__new { width: 100%; }
.ai-chat__sessions { flex: 1; min-height: 0; }
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  color: var(--el-text-color-regular);
  &:hover { background: var(--el-fill-color-light); }
  &.active { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
  &__title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &__actions { display: none; gap: 6px; .el-icon { &:hover { color: var(--el-color-primary); } } }
  &:hover &__actions { display: flex; }
}
.ai-chat__main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.ai-chat__messages { flex: 1; min-height: 0; }
.ai-chat__welcome { height: 100%; display: flex; align-items: center; justify-content: center; }

/* Claude-like document column: centered, generous whitespace */
.ai-thread {
  max-width: 780px;
  margin: 0 auto;
  padding: 28px 20px 40px;
}
.msg { margin-bottom: 24px; }
.msg__role {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
/* User: subtle gray rounded block on the right */
.msg--user { display: flex; justify-content: flex-end; }
.ai-bubble--user :deep(.el-bubble-content),
.ai-bubble--user :deep([class*='content']) {
  background: var(--el-fill-color) !important;
  color: var(--el-text-color-primary) !important;
  border-radius: 14px !important;
}
/* Assistant: borderless, full width, document feel */
.ai-bubble--assistant { width: 100%; }
.ai-bubble--assistant :deep([class*='content']) {
  background: transparent !important;
  padding-left: 0 !important;
  padding-right: 0 !important;
  max-width: 100% !important;
}
.ai-bubble--assistant :deep(.md-editor-preview) { font-size: 15px; line-height: 1.7; }
.ai-thinking { max-width: 780px; margin: 0 auto 24px; }
.ai-chat__pending { color: var(--el-text-color-secondary); font-size: 13px; .spin { animation: spin 1s linear infinite; } }
@keyframes spin { to { transform: rotate(360deg); } }
.file-chip, .attach-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; margin: 2px 4px 6px 0;
  background: var(--el-fill-color-light); border-radius: 4px; font-size: 13px;
}
.attach-chip__x { cursor: pointer; &:hover { color: var(--el-color-danger); } }
.ai-chat__composer {
  border-top: 1px solid var(--el-border-color-light);
  padding: 12px 16px 16px;
}
.ai-chat__composer > * {
  max-width: 780px;
  margin-left: auto;
  margin-right: auto;
}
.composer-attachments { margin-bottom: 6px; }
.composer-bar { display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-top: 8px; }
.preview-head { display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 12px;
  &__name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &__actions { display: flex; gap: 8px; flex-shrink: 0; }
}
.preview-body { height: 100%; overflow: auto; }
</style>
