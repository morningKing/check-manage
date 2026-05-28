<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import {
  ElButton, ElInput, ElScrollbar, ElIcon, ElTooltip, ElEmpty, ElMessageBox, ElMessage,
  ElDrawer,
} from 'element-plus'
import {
  Plus, Promotion, Delete, EditPen, Paperclip, Close, Document, Loading, Download,
} from '@element-plus/icons-vue'
import { Bubble, Thinking } from 'vue-element-plus-x'
import 'vue-element-plus-x/styles/index.css'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import ToolCallBubble from '@/components/ai-chat/ToolCallBubble.vue'
import ArtifactCard from '@/components/ai-chat/ArtifactCard.vue'
import ArtifactPreview, { type ArtifactVersion } from '@/components/ai-chat/ArtifactPreview.vue'
import { splitArtifacts, sniffLang, artifactFilename, type CodeSegment } from '@/utils/artifacts'
import { useAiChatStore } from '@/stores/aiChat'
import { downloadFileUrl, type AiMessage } from '@/api/aiChat'

const store = useAiChatStore()

const input = ref('')
const fileInputEl = ref<HTMLInputElement | null>(null)
const scroller = ref<InstanceType<typeof ElScrollbar> | null>(null)

const sessions = computed(() => store.sessions)
const activeId = computed(() => store.activeSessionId)
const messages = computed(() => store.activeMessages)
const streaming = computed(() => store.isStreaming)
const attachments = computed(() => store.activeAttachments)
const outputs = computed(() => store.activeOutputs)
const reasoning = computed(() => (activeId.value ? store.reasoning[activeId.value] || '' : ''))
const fileUrl = (path: string) => downloadFileUrl(activeId.value || '', path)
const thinking = computed(() => (activeId.value ? !!store.thinking[activeId.value] : false))

const canSend = computed(() => !streaming.value && (input.value.trim() || attachments.value.length))

function hasText(m: AiMessage): boolean {
  return m.content.some(p => p.type === 'text' && p.text)
}

// ---- Artifacts (Claude-style file preview + version history) ----
// Group artifacts by filename across the whole session. Named files (the fence
// info string is a filename) accumulate versions; generic blocks stay singletons.
const versionMap = computed(() => {
  const map = new Map<string, ArtifactVersion[]>()
  for (const m of messages.value) {
    if (m.role !== 'assistant') continue
    for (const p of m.content) {
      if (p.type !== 'text' || !p.text) continue
      let ci = 0
      for (const seg of splitArtifacts(p.text)) {
        if (seg.type !== 'code') continue
        const lang = sniffLang(seg.lang, seg.code)
        const fn = artifactFilename(lang, ci)
        ci++
        const arr = map.get(fn) ?? []
        arr.push({ lang, code: seg.code })
        map.set(fn, arr)
      }
    }
  }
  return map
})
function fileNameOf(seg: CodeSegment, idx: number): string {
  return artifactFilename(sniffLang(seg.lang, seg.code), idx)
}
function versionsForSeg(seg: CodeSegment, idx: number): ArtifactVersion[] {
  const fn = fileNameOf(seg, idx)
  return versionMap.value.get(fn) ?? [{ lang: sniffLang(seg.lang, seg.code), code: seg.code }]
}

const previewOpen = ref(false)
const preview = ref<{ filename: string; versions: ArtifactVersion[] } | null>(null)
function openPreview(seg: CodeSegment, idx: number) {
  preview.value = { filename: fileNameOf(seg, idx), versions: versionsForSeg(seg, idx) }
  previewOpen.value = true
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
                            :versions="versionsForSeg(seg, si).length"
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

            <!-- 思考过程：完成后自动收起 -->
            <Thinking
              v-if="reasoning"
              class="ai-thinking"
              :content="reasoning"
              :status="thinking ? 'thinking' : 'end'"
              :auto-collapse="true"
            />

            <div v-if="streaming && !messages.some(m => m.role === 'assistant' && hasText(m)) && !reasoning" class="ai-chat__pending">
              <ElIcon class="spin"><Loading /></ElIcon> 正在思考…
            </div>

            <!-- 产出文件（agent 写入 outputs/ 的真实文件） -->
            <div v-if="outputs.length" class="ai-outputs">
              <div class="ai-outputs__title">产出文件</div>
              <a
                v-for="f in outputs" :key="f.path"
                class="output-file" :href="fileUrl(f.path)" target="_blank" rel="noopener"
              >
                <ElIcon><Document /></ElIcon>
                <span class="output-file__name">{{ f.name }}</span>
                <span class="output-file__size">{{ (f.size / 1024).toFixed(1) }} KB</span>
                <ElIcon class="output-file__dl"><Download /></ElIcon>
              </a>
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

    <!-- 制品预览面板（Claude 风格，含版本切换） -->
    <ElDrawer v-model="previewOpen" :title="preview?.filename || '预览'" direction="rtl" size="52%">
      <div class="preview-body">
        <ArtifactPreview v-if="preview" :filename="preview.filename" :versions="preview.versions" />
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
/* User: a single gray rounded block on the right (no inner box) */
.msg--user { display: flex; justify-content: flex-end; }
.ai-bubble--user :deep(.elx-bubble__content-wrapper),
.ai-bubble--user :deep(.elx-bubble__content) {
  background: var(--el-fill-color) !important;
  color: var(--el-text-color-primary) !important;
  border: none !important;
  border-radius: 14px !important;
}
/* Assistant: borderless, full width, document feel — no frame around content */
.ai-bubble--assistant { width: 100%; }
.ai-bubble--assistant :deep(.elx-bubble__content-wrapper),
.ai-bubble--assistant :deep(.elx-bubble__content) {
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
  padding: 0 !important;
  max-width: 100% !important;
}
.ai-bubble--assistant :deep(.md-editor-preview) { font-size: 15px; line-height: 1.7; }
.ai-thinking { max-width: 780px; margin: 0 auto 24px; }
.ai-outputs {
  margin: 4px 0 24px;
  padding: 12px 14px;
  border: 1px dashed var(--el-border-color);
  border-radius: 10px;
  background: var(--el-fill-color-lighter);
  &__title { font-size: 13px; font-weight: 600; color: var(--el-text-color-secondary); margin-bottom: 8px; }
}
.output-file {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px; border-radius: 6px; text-decoration: none;
  color: var(--el-text-color-primary); font-size: 14px;
  &:hover { background: var(--el-fill-color); }
  &__name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &__size { font-size: 12px; color: var(--el-text-color-secondary); }
  &__dl { color: var(--el-color-primary); }
}
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
