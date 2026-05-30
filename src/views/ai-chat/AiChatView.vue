<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import {
  ElButton, ElInput, ElScrollbar, ElIcon, ElEmpty, ElMessageBox, ElMessage,
  ElDrawer, ElTag,
  ElDropdown, ElDropdownMenu, ElDropdownItem,
} from 'element-plus'
import {
  Plus, Top, Delete, EditPen, Close, Document, Loading, Download,
  CopyDocument, RefreshRight, Refresh,
} from '@element-plus/icons-vue'
import { Bubble, Thinking } from 'vue-element-plus-x'
import 'vue-element-plus-x/styles/index.css'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import ToolCallBubble from '@/components/ai-chat/ToolCallBubble.vue'
import ArtifactCard from '@/components/ai-chat/ArtifactCard.vue'
import ArtifactPreview, { type ArtifactVersion } from '@/components/ai-chat/ArtifactPreview.vue'
import RunResultBlock from '@/components/ai-chat/RunResultBlock.vue'
import McpServicesBlock from '@/components/ai-chat/McpServicesBlock.vue'
import ChatFile from '@/components/ai-chat/ChatFile.vue'
import QueryResultBlock from '@/components/ai-chat/QueryResultBlock.vue'
import CommandPalette, { type PaletteItem } from '@/components/ai-chat/CommandPalette.vue'
import { findFrontendCommand, parseCommandLine, FRONTEND_COMMANDS } from '@/components/ai-chat/chat-commands'
import { splitArtifacts, sniffLang, artifactFilename, isImageFile, type CodeSegment } from '@/utils/artifacts'
import { useAiChatStore } from '@/stores/aiChat'
import { downloadFileUrl, runScript, type AiMessage, type ChangedFile } from '@/api/aiChat'

const store = useAiChatStore()

const input = ref('')
const activeIndex = ref(0)
const fileInputEl = ref<HTMLInputElement | null>(null)
const scroller = ref<InstanceType<typeof ElScrollbar> | null>(null)

const sessions = computed(() => store.sessions)
const activeId = computed(() => store.activeSessionId)
const palette = computed<PaletteItem[]>(() => {
  const raw = input.value.trimStart()
  if (!raw.startsWith('/')) return []
  const afterSlash = raw.slice(1)
  // The palette only helps pick a command NAME. Once a space follows it (typing
  // args, or right after accepting a command — acceptItem appends a space), close
  // so Enter sends/runs instead of re-accepting the highlighted item.
  if (afterSlash.includes(' ')) return []
  const q = afterSlash.toLowerCase()
  const sid = activeId.value
  const cached = sid ? store.paletteItems[sid] : undefined
  const builtin: PaletteItem[] = FRONTEND_COMMANDS.map((c) => ({ kind: 'builtin', name: c.name, description: c.description }))
  const commands: PaletteItem[] = (cached?.commands ?? []).map((c) => ({ kind: 'command', name: c.name, description: c.description }))
  const skills: PaletteItem[] = (cached?.skills ?? []).map((s) => ({ kind: 'skill', name: s.name, description: s.description }))
  return [...builtin, ...commands, ...skills].filter((it) => !q || it.name.toLowerCase().includes(q))
})
const paletteOpen = computed(() => palette.value.length > 0)
watch(palette, () => { activeIndex.value = 0 })
const messages = computed(() => store.activeMessages)
const streaming = computed(() => store.isStreaming)
const attachments = computed(() => store.activeAttachments)
const outputs = computed(() => store.activeOutputs)
const changes = computed<ChangedFile[]>(() => store.activeChanges)
function changeBadge(status: string): { label: string; type: any } {
  if (status === 'added') return { label: '新增', type: 'success' }
  if (status === 'deleted') return { label: '删除', type: 'info' }
  return { label: '修改', type: 'warning' }
}
async function previewChange(c: ChangedFile) {
  if (c.status === 'deleted') return
  try {
    const text = await fetch(fileUrl(c.path)).then((r) => r.text())
    const lang = (c.path.split('.').pop() || 'txt').toLowerCase()
    preview.value = { filename: c.path, versions: [{ lang, code: text }] }
    previewOpen.value = true
  } catch {
    ElMessage.error('预览失败')
  }
}
const reasoning = computed(() => (activeId.value ? store.reasoning[activeId.value] || '' : ''))
const fileUrl = (path: string) => downloadFileUrl(activeId.value || '', path)
const thinking = computed(() => (activeId.value ? !!store.thinking[activeId.value] : false))

const canSend = computed(() => !streaming.value && (input.value.trim() || attachments.value.length))

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

// User-triggered script run: execute the artifact's code server-side to produce
// the result file, then refresh 产出文件. Keyed by the artifact's code so the
// card shows a per-card running spinner.
const runningCode = ref<string | null>(null)
async function runArtifact(seg: CodeSegment, idx: number) {
  if (!activeId.value || runningCode.value) return
  const sid = activeId.value
  const filename = fileNameOf(seg, idx)
  runningCode.value = seg.code
  try {
    const res = await runScript(sid, seg.code, filename)
    await store.loadFiles(sid)
    // append the run result as a (persisted) message so it stays in history
    store.appendMessage(sid, {
      id: res.messageId || 'run_' + Date.now(),
      role: 'assistant',
      content: [{
        type: 'run_result', filename,
        exitCode: res.exitCode, timedOut: res.timedOut,
        stdout: res.stdout, stderr: res.stderr, outputFiles: res.outputFiles,
      }],
    })
    if (res.exitCode === 0) {
      const n = res.outputFiles.length
      ElMessage.success(n ? `运行完成，生成 ${n} 个文件` : '运行完成')
    } else {
      ElMessage.error('脚本运行出错，详见运行结果')
    }
    scrollToBottom()
  } catch {
    ElMessage.error('运行失败')
  } finally {
    runningCode.value = null
  }
}

function isRunResultOnly(m: AiMessage): boolean {
  return m.content.length > 0 && m.content.every(p => p.type === 'run_result')
}

function parseQueryResult(p: any): any | null {
  if (p?.name !== 'query_collection') return null
  let r: any = p.result
  if (typeof r === 'string') {
    try { r = JSON.parse(r) } catch { return null }
  }
  return r && typeof r === 'object' && typeof r.mode === 'string' ? r : null
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

const skillInput = ref<HTMLInputElement | null>(null)

function handleAddMenu(cmd: string) {
  if (cmd === 'file') pickFiles()
  else if (cmd === 'skill') skillInput.value?.click()
}

// Backend emits a specific `code` for every validation failure; map them to
// friendly Chinese hints so the user can act on the error (instead of a
// generic "技能添加失败").
const SKILL_ERR_MSG: Record<string, string> = {
  SKILL_EXISTS: '该技能已存在；请改 zip 里的 name 后再传',
  SKILL_ZIP_INVALID: 'zip 文件已损坏',
  SKILL_ZIP_TOO_LARGE: 'zip 超过 5 MiB 上限',
  SKILL_ZIP_TOO_MANY_FILES: 'zip 文件数过多（>200）',
  SKILL_ZIP_UNSAFE: 'zip 含非法路径，已拒绝',
  INVALID_SKILL_ZIP: 'zip 里缺少 SKILL.md',
  INVALID_SKILL_NAME: '技能名只能包含字母/数字/下划线/短横线',
  BAD_FILE: '请选择一个 .zip 文件',
}

async function onSkillPicked(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  input.value = ''
  if (!f) return
  if (!activeId.value) await newSession()
  try {
    const res = await store.uploadSkill(f)
    ElMessage.success(`已添加技能：${res.name}`)
  } catch (err: any) {
    const code: string | undefined = err?.response?.data?.code
    ElMessage.error(
      (code && SKILL_ERR_MSG[code]) || err?.response?.data?.error || '技能添加失败',
    )
  }
}

function acceptItem(item: PaletteItem) {
  if (item.kind === 'skill') input.value = '使用 `' + item.name + '` 技能:'
  else input.value = '/' + item.name + ' '
  activeIndex.value = 0
}

// User-bubble Copy / Edit / Retry. Edit + Retry are destructive (delete the
// message and everything after it), so they go through ElMessageBox.confirm.
function messageText(m: AiMessage): string {
  return m.content
    .filter((p) => p.type === 'text' && (p as any).text)
    .map((p) => (p as any).text as string)
    .join('\n')
}

async function copyMessage(m: AiMessage) {
  const text = messageText(m)
  if (!text) return
  try { await navigator.clipboard.writeText(text); ElMessage.success('已复制') }
  catch { ElMessage.error('复制失败') }
}

async function editMessage(m: AiMessage) {
  const text = messageText(m)
  if (!activeId.value) return
  if (m.id.startsWith('local_')) { ElMessage.warning('消息还未保存，请稍后重试'); return }
  try {
    await ElMessageBox.confirm('将删除该消息及其之后的全部对话，然后把文本载入输入框便于修改。继续？',
      '编辑这条消息', { confirmButtonText: '继续', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  await store.deleteFromMessage(activeId.value, m.id)
  input.value = text
}

async function retryMessage(m: AiMessage) {
  if (!activeId.value) return
  if (m.id.startsWith('local_')) { ElMessage.warning('消息还未保存，请稍后重试'); return }
  try {
    await ElMessageBox.confirm('将删除该消息及其之后的全部对话，然后重新发送。继续？',
      '重新生成回答', { confirmButtonText: '继续', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  try { await store.retryUserMessage(m.id) } catch { ElMessage.error('重新发送失败') }
}

// 手动重新扫描「变更文件」面板。Skill 写出的新文件如果没落在某个 git 仓库里、
// 或者扫描时刚好赶在 session.idle 之前的窗口里，可能会漏 —— 给用户一个明确的
// "再扫一遍" 出口。
const changesLoading = ref(false)
async function refreshChanges() {
  if (!activeId.value) return
  changesLoading.value = true
  try { await store.loadChanges(activeId.value) }
  finally { changesLoading.value = false }
}

async function send() {
  if (!canSend.value) return
  const text = input.value.trim()
  input.value = ''
  if (!activeId.value) await newSession()
  const sid = activeId.value!
  const parsed = parseCommandLine(text)
  if (parsed) {
    const fc = findFrontendCommand(parsed.name)
    if (fc) { await fc.run(store); return }
    if (store.isOpencodeCommand(sid, parsed.name)) {
      try { await store.runCommand(sid, parsed.name, parsed.args) } catch { ElMessage.error('执行失败') }
      return
    }
    // unknown /xxx → fall through to a normal message
  }
  try { await store.sendUserMessage(text) } catch { ElMessage.error('发送失败') }
}

function onKey(e: Event) {
  const ev = e as KeyboardEvent
  if (paletteOpen.value) {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); activeIndex.value = (activeIndex.value + 1) % palette.value.length; return }
    if (ev.key === 'ArrowUp') { ev.preventDefault(); activeIndex.value = (activeIndex.value - 1 + palette.value.length) % palette.value.length; return }
    if (ev.key === 'Enter' || ev.key === 'Tab') { ev.preventDefault(); acceptItem(palette.value[activeIndex.value]); return }
  }
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
      <div v-if="activeId && store.activeStreamStatus === 'reconnecting'" class="ai-chat__reconnect">
        <ElIcon class="spin"><Loading /></ElIcon> 与服务端连接断开，正在重连…
      </div>
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
              <div class="msg__role" v-if="m.role !== 'user' && !isRunResultOnly(m)">AI 助手</div>
              <Bubble
                :placement="m.role === 'user' ? 'end' : 'start'"
                :variant="m.role === 'user' ? 'filled' : 'borderless'"
                :class="['ai-bubble', 'ai-bubble--' + m.role]"
              >
                <template #content>
                  <template v-for="(p, i) in m.content" :key="i">
                    <ChatFile v-if="p.type === 'file'" :name="p.name" :src="fileUrl(p.path)" />
                    <template v-else-if="p.type === 'tool_use'">
                      <QueryResultBlock
                        v-if="parseQueryResult(p)"
                        :result="parseQueryResult(p)!" :download-url="fileUrl"
                      />
                      <ToolCallBubble
                        v-else
                        :name="p.name" :title="p.title" :status="p.status"
                        :input="p.input" :result="p.result"
                      />
                    </template>
                    <RunResultBlock
                      v-else-if="p.type === 'run_result'"
                      :result="p" :download-url="fileUrl"
                    />
                    <McpServicesBlock
                      v-else-if="p.type === 'mcp_services'"
                      :servers="p.servers"
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
                            :running="runningCode === seg.code"
                            @preview="openPreview(seg, si)"
                            @run="runArtifact(seg, si)"
                          />
                        </template>
                      </template>
                      <MarkdownView v-else :text="p.text" />
                    </template>
                  </template>
                </template>
              </Bubble>
              <div v-if="m.role === 'user' && messageText(m)" class="msg__actions">
                <button
                  class="msg__action-btn" type="button" title="复制" aria-label="复制"
                  @click="copyMessage(m)"
                ><ElIcon><CopyDocument /></ElIcon></button>
                <button
                  class="msg__action-btn" type="button" title="编辑并重发" aria-label="编辑并重发"
                  :disabled="streaming" @click="editMessage(m)"
                ><ElIcon><EditPen /></ElIcon></button>
                <button
                  class="msg__action-btn" type="button" title="重新生成" aria-label="重新生成"
                  :disabled="streaming" @click="retryMessage(m)"
                ><ElIcon><RefreshRight /></ElIcon></button>
              </div>
            </div>

            <!-- 思考过程：完成后自动收起 -->
            <Thinking
              v-if="reasoning"
              class="ai-thinking"
              :content="reasoning"
              :status="thinking ? 'thinking' : 'end'"
              :auto-collapse="true"
            />

            <!-- Show "thinking" until the assistant's first part arrives in THIS
                 turn: streaming + no reasoning visible + the latest message is
                 still the user's. The previous `.some(... hasText)` over all
                 history hid the spinner forever after the first reply. -->
            <div
              v-if="streaming && !reasoning && messages[messages.length - 1]?.role === 'user'"
              class="ai-chat__pending"
            >
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

            <!-- 变更文件（会话 workspace 内 git 仓库的新增/修改/删除） -->
            <div v-if="activeId" class="ai-changes">
              <div class="ai-changes__title">
                <span>变更文件 <span v-if="changes.length" class="ai-changes__count">({{ changes.length }})</span></span>
                <button
                  class="ai-changes__refresh" type="button"
                  title="重新扫描变更文件" aria-label="重新扫描变更文件"
                  :disabled="changesLoading"
                  @click="refreshChanges"
                >
                  <ElIcon :class="{ spin: changesLoading }"><Refresh /></ElIcon>
                </button>
              </div>
              <div v-if="!changes.length" class="ai-changes__empty">暂无变更（点击 🔄 重新扫描）</div>
              <div v-for="c in changes" :key="c.path" class="change-file">
                <div class="change-file__row">
                  <ElTag size="small" :type="changeBadge(c.status).type">{{ changeBadge(c.status).label }}</ElTag>
                  <span class="change-file__name">{{ c.path }}</span>
                  <ElButton v-if="c.status !== 'deleted'" size="small" text @click="previewChange(c)">预览</ElButton>
                  <a
                    v-if="c.status !== 'deleted'"
                    class="change-file__dl" :href="fileUrl(c.path)" target="_blank" rel="noopener"
                  >下载</a>
                </div>
                <a
                  v-if="c.status !== 'deleted' && isImageFile(c.path)"
                  class="change-file__img" :href="fileUrl(c.path)" target="_blank" rel="noopener noreferrer"
                >
                  <img :src="fileUrl(c.path)" :alt="c.path" />
                </a>
              </div>
            </div>
          </div>
        </template>
      </ElScrollbar>

      <!-- 输入区：统一圆角卡片（Claude 风格） -->
      <div class="ai-chat__composer">
        <CommandPalette :items="palette" :active-index="activeIndex" @select="acceptItem" />
        <div class="composer-inner">
          <div class="composer-card">
            <div v-if="attachments.length" class="composer-attachments">
              <span v-for="a in attachments" :key="a.path" class="attach-chip">
                <ElIcon><Document /></ElIcon>{{ a.name }}
                <ElIcon class="attach-chip__x" @click="store.removeAttachment(a.path)"><Close /></ElIcon>
              </span>
            </div>
            <ElInput
              v-model="input" type="textarea" :autosize="{ minRows: 1, maxRows: 8 }"
              class="composer-input"
              placeholder="给 AI 助手发消息…（Enter 发送，Shift+Enter 换行）"
              @keydown="onKey"
            />
            <div class="composer-bar">
              <div class="composer-bar__left">
                <input ref="fileInputEl" type="file" multiple hidden @change="onFilesPicked" />
                <input ref="skillInput" type="file" accept=".zip" hidden @change="onSkillPicked" />
                <ElDropdown trigger="click" @command="handleAddMenu">
                  <ElButton
                    class="composer-add" :icon="Plus" circle text
                    :loading="store.uploading"
                    aria-label="添加附件或技能" title="添加附件或技能"
                  />
                  <template #dropdown>
                    <ElDropdownMenu>
                      <ElDropdownItem command="file" :disabled="store.uploading">上传附件</ElDropdownItem>
                      <ElDropdownItem command="skill" :disabled="store.uploading">添加技能 (zip)</ElDropdownItem>
                    </ElDropdownMenu>
                  </template>
                </ElDropdown>
              </div>
              <div class="composer-bar__right">
                <span class="composer-model">MiMo</span>
                <ElButton
                  v-if="streaming"
                  class="composer-send composer-send--stop" type="danger" circle :icon="Close"
                  title="停止生成" aria-label="停止生成"
                  @click="store.abortStreaming()"
                />
                <ElButton
                  v-else
                  class="composer-send" type="primary" circle :icon="Top"
                  :disabled="!canSend" @click="send"
                  title="发送" aria-label="发送"
                />
              </div>
            </div>
          </div>
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
.ai-chat__reconnect {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 16px; font-size: 13px;
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning-dark-2);
  border-bottom: 1px solid var(--el-color-warning-light-7);
  .spin { animation: spin 1s linear infinite; }
}

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
.msg--user { display: flex; flex-direction: column; align-items: flex-end; }
.msg__actions {
  display: flex; gap: 4px;
  margin-top: 4px;
  opacity: 0;
  transition: opacity 0.15s ease;
}
.msg--user:hover .msg__actions { opacity: 0.9; }
.msg__action-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px;
  padding: 0;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color);
  color: var(--el-text-color-regular);
  font-size: 13px;
  cursor: pointer;
}
.msg__action-btn:hover:not(:disabled) {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}
.msg__action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
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
  /* Full width (not shrink-to-content) so 100%-width children like echarts
     charts don't collapse to a tiny default size. */
  width: 100% !important;
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
  padding: 8px 16px 18px;
  position: relative;
}
/* one centered column */
.composer-inner {
  max-width: 780px;
  margin: 0 auto;
}
/* unified rounded card holding attachments + input + actions (Claude-style) */
.composer-card {
  border: 1px solid var(--el-border-color);
  border-radius: 20px;
  background: var(--el-bg-color);
  padding: 10px 14px 8px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.composer-card:focus-within {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 3px var(--el-color-primary-light-8);
}
/* borderless input that blends into the card */
.composer-input :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none !important;
  background: transparent;
  padding: 4px 4px 0;
  resize: none;
  font-size: 15px;
  line-height: 1.6;
}
.composer-attachments { margin-bottom: 4px; }
.composer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
}
.composer-bar__right { display: flex; align-items: center; gap: 10px; }
.composer-model { font-size: 13px; color: var(--el-text-color-secondary); }
.composer-add {
  color: var(--el-text-color-secondary);
  font-size: 18px;
  &:hover { color: var(--el-color-primary); background: var(--el-fill-color); }
}
.composer-send { font-size: 16px; }
.preview-head { display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 12px;
  &__name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &__actions { display: flex; gap: 8px; flex-shrink: 0; }
}
.preview-body { height: 100%; overflow: auto; }
.ai-changes {
  margin: 4px 0 24px;
  padding: 12px 14px;
  border: 1px dashed var(--el-border-color);
  border-radius: 10px;
  background: var(--el-fill-color-lighter);
  &__title {
    display: flex; align-items: center; gap: 6px;
    font-size: 13px; font-weight: 600; color: var(--el-text-color-secondary);
    margin-bottom: 8px;
  }
  &__count { font-weight: 400; color: var(--el-text-color-secondary); }
  &__empty { font-size: 12px; color: var(--el-text-color-secondary); padding: 4px 0; }
  &__refresh {
    margin-left: auto;
    display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px;
    padding: 0;
    background: transparent;
    border: none;
    color: var(--el-text-color-secondary);
    cursor: pointer;
    border-radius: 4px;
    .spin { animation: spin 0.8s linear infinite; }
    &:hover:not(:disabled) {
      background: var(--el-fill-color);
      color: var(--el-color-primary);
    }
    &:disabled { opacity: 0.5; cursor: not-allowed; }
  }
}
.change-file {
  display: flex; flex-direction: column; gap: 6px;
  padding: 6px 8px; border-radius: 6px; font-size: 14px;
  &:hover { background: var(--el-fill-color); }
  &__row { display: flex; align-items: center; gap: 8px; }
  &__name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--el-font-family-mono, monospace); }
  &__dl { color: var(--el-color-primary); text-decoration: none; font-size: 13px; }
  &__img {
    display: block;
    margin-left: 56px; /* line up under the filename, past the status tag */
    text-decoration: none;
    img {
      display: block;
      max-width: 100%; max-height: 240px;
      border: 1px solid var(--el-border-color); border-radius: 6px;
      background: var(--el-bg-color);
    }
  }
}
</style>
