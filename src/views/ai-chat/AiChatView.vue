<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import {
  ElButton, ElInput, ElScrollbar, ElIcon, ElEmpty, ElMessageBox, ElMessage,
  ElDrawer, ElTag,
  ElDropdown, ElDropdownMenu, ElDropdownItem,
  ElSelect, ElOption,
} from 'element-plus'
import {
  Plus, Top, EditPen, Close, Document, Loading,
  CopyDocument, RefreshRight, Refresh, ArrowRight, Delete, Brush,
} from '@element-plus/icons-vue'
import { Bubble, Thinking } from 'vue-element-plus-x'
import 'vue-element-plus-x/styles/index.css'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import ToolCallBubble from '@/components/ai-chat/ToolCallBubble.vue'
import TodoListBlock from '@/components/ai-chat/TodoListBlock.vue'
import { parseTodos } from '@/utils/todos'
import ArtifactCard from '@/components/ai-chat/ArtifactCard.vue'
import ArtifactPreview, { type ArtifactVersion } from '@/components/ai-chat/ArtifactPreview.vue'
import RunResultBlock from '@/components/ai-chat/RunResultBlock.vue'
import McpServicesBlock from '@/components/ai-chat/McpServicesBlock.vue'
import ChatFile from '@/components/ai-chat/ChatFile.vue'
import QueryResultBlock from '@/components/ai-chat/QueryResultBlock.vue'
import CommandPalette, { type PaletteItem } from '@/components/ai-chat/CommandPalette.vue'
import FileDiffView from '@/components/ai-chat/FileDiffView.vue'
import { findFrontendCommand, parseCommandLine, FRONTEND_COMMANDS } from '@/components/ai-chat/chat-commands'
import { splitArtifacts, sniffLang, artifactFilename, isImageFile, groupFilesByDir, type CodeSegment } from '@/utils/artifacts'
import { activeMentionToken } from '@/utils/agentMentions'
import { copyText } from '@/utils/clipboard'
import { summarizeMeta } from '@/utils/aiMeta'
import { useAiChatStore } from '@/stores/aiChat'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import BatchGroup from '@/components/ai-chat/BatchGroup.vue'
import CreateBatchDialog from '@/components/ai-chat/CreateBatchDialog.vue'
import PromptTemplateManager from '@/components/ai-chat/PromptTemplateManager.vue'
import MemoryManager from '@/components/ai-chat/MemoryManager.vue'
import { downloadFileUrl, runScript, listModels, listAgents, getFileDiff, getFilePreview, expandChangeDir, type AiMessage, type ChangedFile, type ModelInfo, type AgentInfo, type FileDiff } from '@/api/aiChat'

const store = useAiChatStore()
const batches = useAiChatBatchesStore()

const showCreateBatch = ref(false)
const showTemplateManager = ref(false)
const showMemoryManager = ref(false)

// Composer model picker: list comes from OpenCode's /provider via the
// backend; selection is per-session and persisted in localStorage via
// the store's setSessionModel / hydrateSessionModel actions.
const models = ref<ModelInfo[]>([])
const modelsLoading = ref(false)
async function fetchModels() {
  if (modelsLoading.value) return
  modelsLoading.value = true
  try {
    const r = await listModels()
    models.value = r.models
  } catch { /* surfaced by interceptor */ }
  finally { modelsLoading.value = false }
}
const composerModel = computed<string>({
  get: () => (activeId.value ? store.modelBySession[activeId.value] || '' : ''),
  set: (v) => { if (activeId.value) store.setSessionModel(activeId.value, v) },
})

// Composer agent picker: list from OpenCode's /agent via backend; selection is
// per-session and persisted via the store's setSessionAgent / hydrateSessionAgent.
const agents = ref<AgentInfo[]>([])
const agentsLoading = ref(false)
async function fetchAgents() {
  if (agentsLoading.value) return
  agentsLoading.value = true
  try {
    const r = await listAgents()
    agents.value = r.agents
    store.subagents = r.subagents
  } catch { /* surfaced by interceptor */ }
  finally { agentsLoading.value = false }
}
const composerAgent = computed<string>({
  get: () => (activeId.value ? store.agentBySession[activeId.value] || '' : ''),
  set: (v) => { if (activeId.value) store.setSessionAgent(activeId.value, v) },
})


const input = ref('')
const cursorPos = ref(0)
const composerInputEl = ref<InstanceType<typeof ElInput> | null>(null)
function syncCursor(e: Event) {
  const el = e.target as HTMLTextAreaElement
  cursorPos.value = el.selectionStart ?? input.value.length
}
// When the model changes programmatically (acceptItem / paste), try to read
// selectionStart from the underlying textarea after the DOM settles.
watch(input, async () => {
  await nextTick()
  const textarea = composerInputEl.value?.textarea
  if (textarea) cursorPos.value = textarea.selectionStart ?? input.value.length
})
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
const mentionToken = computed(() => activeMentionToken(input.value, cursorPos.value))
const mentionPalette = computed<PaletteItem[]>(() => {
  const tok = mentionToken.value
  if (!tok) return []
  const q = tok.query.toLowerCase()
  return store.subagents
    .filter((a) => !q || a.name.toLowerCase().includes(q))
    .map((a) => ({ kind: 'agent' as const, name: a.name, description: a.description }))
})
// mention token present → show mention palette; otherwise the `/` command palette
const activePalette = computed<PaletteItem[]>(() => (mentionToken.value ? mentionPalette.value : palette.value))
const paletteOpen = computed(() => activePalette.value.length > 0)
watch(activePalette, () => { activeIndex.value = 0 })
const messages = computed(() => store.activeMessages)
const streaming = computed(() => store.isStreaming)
const attachments = computed(() => store.activeAttachments)
const outputs = computed(() => store.activeOutputs)
// 产出文件按目录分组，每个目录一个可折叠分组（默认展开）。
const groupedOutputs = computed(() => groupFilesByDir(outputs.value))
const outputsCollapsed = reactive<Record<string, boolean>>({})
function toggleOutputGroup(dir: string) { outputsCollapsed[dir] = !outputsCollapsed[dir] }
const changes = computed<ChangedFile[]>(() => store.activeChanges)

// 变更面板只展示新增/修改，不列出删除（后端已过滤 deleted）
const groupedChanges = computed(() => ({
  added: changes.value.filter((c) => c.status === 'added'),
  modified: changes.value.filter((c) => c.status === 'modified'),
}))

const collapsed = reactive<Record<'added' | 'modified', boolean>>({
  added: false,
  modified: false,
})
function toggleGroup(k: 'added' | 'modified') { collapsed[k] = !collapsed[k] }

const GROUP_META: { key: 'added' | 'modified'; label: string; type: any }[] = [
  { key: 'added', label: '新增', type: 'success' },
  { key: 'modified', label: '修改', type: 'warning' },
]
async function previewChange(c: ChangedFile) {
  if (c.status === 'deleted' || !activeId.value) return
  diffFile.value = c.path
  diffData.value = null
  diffOpen.value = true
  diffLoading.value = true
  try {
    const res = await getFileDiff(activeId.value, c.path)
    diffData.value = res
    // status null = this path is no longer a current change (the panel list
    // drifted out of sync with the workspace — e.g. the file was reverted or
    // removed since the last scan). Re-scan so the stale row disappears; the
    // drawer shows a clear "no diff" message instead of dead-ending.
    if (res.status === null) store.loadChanges(activeId.value)
  } catch {
    ElMessage.error('预览失败')
    diffOpen.value = false
  } finally {
    diffLoading.value = false
  }
}
// 产出文件预览：复用 diff 抽屉，但走 git-independent 的 /preview 读文件内容。
async function previewOutput(f: { name: string; path: string }) {
  if (!activeId.value) return
  diffFile.value = f.path
  diffData.value = null
  diffOpen.value = true
  diffLoading.value = true
  try {
    const r = await getFilePreview(activeId.value, f.path)
    diffData.value = r.binary
      ? { status: 'added', content: '（二进制文件，无法预览，请下载查看）', truncated: false }
      : { status: 'added', content: r.content, truncated: r.truncated }
  } catch {
    ElMessage.error('预览失败')
    diffOpen.value = false
  } finally {
    diffLoading.value = false
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
const diffOpen = ref(false)
const diffData = ref<FileDiff | null>(null)
const diffFile = ref('')
const diffLoading = ref(false)
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

// Live view for a running batch child: its work is persisted incrementally on
// the server but not pushed over SSE, so poll its messages while it runs. The
// watch keys on activeId AND the batch children's statuses, so it (re)starts
// when the child's status becomes known/running and stops on completion (with a
// final refresh). Keying only on activeId would miss the case where
// activeSessions loads after the child is opened.
let liveTimer: ReturnType<typeof setInterval> | null = null
function stopLivePoll() { if (liveTimer) { clearInterval(liveTimer); liveTimer = null } }
watch(
  [activeId, () => batches.activeSessions.map((s) => `${s.id}:${s.status}`).join('|')],
  () => {
    stopLivePoll()
    const id = activeId.value
    if (!id) return
    const child = batches.activeSessions.find((s) => s.id === id)
    if (!child) return                       // not a child of the selected batch
    store.reloadMessages(id)                  // reflect latest (covers completion)
    if (child.status === 'running') {
      liveTimer = setInterval(() => store.reloadMessages(id), 2500)
    }
  },
  { immediate: true },
)
onUnmounted(stopLivePoll)

onMounted(async () => {
  try {
    await store.loadSessions()
    if (sessions.value.length) {
      await store.openSession(sessions.value[0].id)
      store.hydrateSessionModel(sessions.value[0].id)
      store.hydrateSessionAgent(sessions.value[0].id)
    }
  } catch { /* surfaced by interceptor */ }
  // pre-fetch models and agents for the dropdowns (best-effort)
  fetchModels()
  fetchAgents()
  // load batch list for the unified sidebar
  batches.fetchList().then(() => batches.startListPolling()).catch(() => {})
})
onUnmounted(() => batches.stopListPolling())

async function newSession() {
  try { await store.startNewSession() } catch { ElMessage.error('创建会话失败') }
}
async function selectSession(id: string) {
  if (id !== activeId.value) await store.openSession(id)
  store.hydrateSessionModel(id)
  store.hydrateSessionAgent(id)
}
async function renameSession(id: string, current: string) {
  try {
    const res = await ElMessageBox.prompt('重命名会话', '重命名', { inputValue: current })
    const value = (res as { value?: string }).value
    if (value?.trim()) await store.renameSession(id, value.trim())
  } catch { /* cancelled */ }
}
async function closeSessionItem(id: string) {
  try {
    await ElMessageBox.confirm('关闭该会话？关闭后可重新打开，历史会保留。', '关闭会话', {
      confirmButtonText: '关闭', cancelButtonText: '取消',
    })
    await store.closeSession(id)
  } catch { /* cancelled */ }
}
async function reopenSessionItem(id: string) {
  try {
    await store.reopenSession(id)
    await selectSession(id)
  } catch { ElMessage.error('重开会话失败') }
}
async function deleteSessionItem(id: string) {
  try {
    await ElMessageBox.confirm('删除该会话？删除后不可恢复，会话与历史将不再可见。', '删除会话', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    await store.deleteSession(id)
    ElMessage.success('已删除')
  } catch (e: unknown) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error('删除会话失败')
  }
}
async function clearSessionItem(id: string) {
  try {
    await ElMessageBox.confirm('清空该会话？将清空对话历史和工作区内的全部文件，不可恢复；会话本身保留，可继续使用。', '清空会话', {
      type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消',
    })
    await store.clearSession(id)
    ElMessage.success('已清空')
  } catch (e: unknown) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error('清空会话失败')
  }
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
  else if (cmd === 'memory') showMemoryManager.value = true
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
  if (item.kind === 'agent') {
    const tok = activeMentionToken(input.value, cursorPos.value)
    if (tok) {
      const before = input.value.slice(0, tok.start)
      const after = input.value.slice(tok.end)
      const insert = '@' + item.name + ' '
      input.value = before + insert + after
      cursorPos.value = before.length + insert.length
    }
    return
  }
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
  if (await copyText(text)) ElMessage.success('已复制')
  else ElMessage.error('复制失败')
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

// Assistant-bubble retry: find the user message that produced this answer
// and re-run it (delete from there + resend).
function previousUserMessage(m: AiMessage): AiMessage | undefined {
  const arr = messages.value
  const idx = arr.findIndex((x) => x.id === m.id)
  if (idx <= 0) return undefined
  for (let i = idx - 1; i >= 0; i--) {
    if (arr[i].role === 'user') return arr[i]
  }
  return undefined
}

async function retryAssistantMessage(m: AiMessage) {
  if (!activeId.value) return
  const userMsg = previousUserMessage(m)
  if (!userMsg) { ElMessage.warning('找不到对应的用户消息'); return }
  if (userMsg.id.startsWith('local_')) { ElMessage.warning('消息还未保存，请稍后重试'); return }
  try {
    await ElMessageBox.confirm('将删除该回答及其之后的对话，然后重新生成。继续？',
      '重新生成回答', { confirmButtonText: '继续', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  try { await store.retryUserMessage(userMsg.id) } catch { ElMessage.error('重新发送失败') }
}

// 手动重新扫描「变更文件」面板。Skill 写出的新文件如果没落在某个 git 仓库里、
// 或者扫描时刚好赶在 session.idle 之前的窗口里，可能会漏 —— 给用户一个明确的
// "再扫一遍" 出口。
const changesLoading = ref(false)
async function refreshChanges() {
  if (!activeId.value) return
  changesLoading.value = true
  try {
    const ok = await store.loadChanges(activeId.value)
    // A failed/incomplete scan keeps the last good list (store guards it) —
    // tell the user so they know the panel wasn't refreshed, rather than
    // silently showing stale or empty data.
    if (ok === false) ElMessage.warning('扫描变更未完成（git 可能繁忙），已保留上次结果，请稍后重试')
  } finally { changesLoading.value = false }
}

// 折叠的「目录/ (N 个新文件)」条目可就地展开看里面的文件（点击懒加载）。
const expandedDirs = reactive<Record<string, { open: boolean; loading: boolean; files: ChangedFile[] }>>({})
async function toggleDir(c: ChangedFile) {
  if (!activeId.value) return
  // Read back through the reactive proxy (an assignment expression returns the
  // raw object, whose mutations wouldn't trigger updates).
  if (!expandedDirs[c.path]) expandedDirs[c.path] = { open: false, loading: false, files: [] }
  const st = expandedDirs[c.path]
  st.open = !st.open
  if (st.open && !st.files.length && !st.loading) {
    st.loading = true
    try {
      const { files } = await expandChangeDir(activeId.value, c.path)
      st.files = files
    } catch { ElMessage.error('展开目录失败') } finally { st.loading = false }
  }
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
    if (ev.key === 'ArrowDown') { ev.preventDefault(); activeIndex.value = (activeIndex.value + 1) % activePalette.value.length; return }
    if (ev.key === 'ArrowUp') { ev.preventDefault(); activeIndex.value = (activeIndex.value - 1 + activePalette.value.length) % activePalette.value.length; return }
    if (ev.key === 'Enter' || ev.key === 'Tab') { ev.preventDefault(); acceptItem(activePalette.value[activeIndex.value]); return }
  }
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); send() }
}
</script>

<template>
  <div class="ai-chat">
    <!-- 会话侧栏 -->
    <aside class="ai-chat__sidebar">
      <div class="ai-sidebar__sessions-wrap">
        <ElButton class="ai-chat__new" type="primary" :icon="Plus" @click="newSession">新建会话</ElButton>
        <ElScrollbar class="ai-chat__sessions">
          <div
            v-for="s in sessions" :key="s.id"
            class="session-item" :class="{ active: s.id === activeId, 'is-closed': s.status === 'closed' }"
            @click="selectSession(s.id)"
          >
            <span class="session-item__title">{{ s.title || '新会话' }}</span>
            <span class="session-item__actions" @click.stop>
              <ElIcon @click="renameSession(s.id, s.title)"><EditPen /></ElIcon>
              <ElIcon v-if="s.status === 'closed'" title="重开会话" @click="reopenSessionItem(s.id)"><RefreshRight /></ElIcon>
              <ElIcon v-else title="关闭会话" @click="closeSessionItem(s.id)"><Close /></ElIcon>
              <ElIcon title="清空会话（清空历史和工作区文件）" @click="clearSessionItem(s.id)"><Brush /></ElIcon>
              <ElIcon title="删除会话" @click="deleteSessionItem(s.id)"><Delete /></ElIcon>
            </span>
          </div>
          <ElEmpty v-if="!sessions.length" description="暂无会话" :image-size="48" />

          <div class="ai-sidebar__batches-head">
            批任务
            <ElButton link size="small" :icon="Plus" @click="showCreateBatch = true">新建</ElButton>
          </div>
          <BatchGroup
            v-for="b in batches.items" :key="b.id"
            :batch="b" :active-session-id="activeId"
            @select-child="selectSession"
          />
          <ElEmpty v-if="!batches.items.length" description="暂无批任务" :image-size="48" />
        </ElScrollbar>
      </div>
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
                      <TodoListBlock
                        v-if="parseTodos(p)"
                        :todos="parseTodos(p)!"
                      />
                      <QueryResultBlock
                        v-else-if="parseQueryResult(p)"
                        :result="parseQueryResult(p)!" :download-url="fileUrl"
                      />
                      <ToolCallBubble
                        v-else
                        :name="p.name" :title="p.title" :status="p.status"
                        :input="p.input" :result="p.result" :duration-ms="p.durationMs"
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
              <div v-if="m.role === 'assistant' && summarizeMeta(m.meta)" class="msg__meta">
                {{ summarizeMeta(m.meta) }}
              </div>
              <div v-if="messageText(m)" class="msg__actions" :class="`msg__actions--${m.role}`">
                <button
                  class="msg__action-btn" type="button" title="复制" aria-label="复制"
                  @click="copyMessage(m)"
                ><ElIcon><CopyDocument /></ElIcon></button>
                <template v-if="m.role === 'user'">
                  <button
                    class="msg__action-btn" type="button" title="编辑并重发" aria-label="编辑并重发"
                    :disabled="streaming" @click="editMessage(m)"
                  ><ElIcon><EditPen /></ElIcon></button>
                  <button
                    class="msg__action-btn" type="button" title="重新生成" aria-label="重新生成"
                    :disabled="streaming" @click="retryMessage(m)"
                  ><ElIcon><RefreshRight /></ElIcon></button>
                </template>
                <button
                  v-else-if="m.role === 'assistant'"
                  class="msg__action-btn" type="button" title="重新生成" aria-label="重新生成"
                  :disabled="streaming" @click="retryAssistantMessage(m)"
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

            <!-- 产出文件（agent 写入 outputs/ 或 workspace 根目录的真实文件） -->
            <div v-if="outputs.length" class="ai-outputs">
              <div class="ai-outputs__title">产出文件 <span class="ai-outputs__count">({{ outputs.length }})</span></div>
              <div v-for="g in groupedOutputs" :key="g.dir" class="output-group">
                <button class="output-group__head" type="button" @click="toggleOutputGroup(g.dir)">
                  <ElIcon class="output-group__chev" :class="{ open: !outputsCollapsed[g.dir] }"><ArrowRight /></ElIcon>
                  <span class="output-group__dir">{{ g.label }}</span>
                  <span class="output-group__count">{{ g.files.length }}</span>
                </button>
                <div v-show="!outputsCollapsed[g.dir]" class="output-group__body">
                  <div v-for="f in g.files" :key="f.path" class="output-file">
                    <ElIcon><Document /></ElIcon>
                    <span class="output-file__name">{{ f.name }}</span>
                    <span class="output-file__size">{{ (f.size / 1024).toFixed(1) }} KB</span>
                    <ElButton size="small" text @click="previewOutput(f)">预览</ElButton>
                    <a class="output-file__dl" :href="fileUrl(f.path)" target="_blank" rel="noopener">下载</a>
                  </div>
                </div>
              </div>
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
              <template v-else>
                <div v-for="g in GROUP_META" :key="g.key" class="change-group">
                  <template v-if="groupedChanges[g.key].length">
                    <button class="change-group__head" type="button" @click="toggleGroup(g.key)">
                      <ElIcon class="change-group__chev" :class="{ open: !collapsed[g.key] }"><ArrowRight /></ElIcon>
                      <ElTag size="small" :type="g.type">{{ g.label }}</ElTag>
                      <span class="change-group__count">{{ groupedChanges[g.key].length }}</span>
                    </button>
                    <div v-show="!collapsed[g.key]" class="change-group__body">
                      <div v-for="c in groupedChanges[g.key]" :key="c.path" class="change-file">
                        <!-- 折叠目录：可点击就地展开里面的文件 -->
                        <template v-if="c.kind === 'dir'">
                          <button class="change-file__dirhead" type="button" @click="toggleDir(c)">
                            <ElIcon class="change-file__chev" :class="{ open: expandedDirs[c.path]?.open }"><ArrowRight /></ElIcon>
                            <span class="change-file__name">{{ c.path }}</span>
                            <span class="change-file__dircount">{{ c.count }} 个新文件</span>
                          </button>
                          <div v-show="expandedDirs[c.path]?.open" class="change-file__dirbody">
                            <span v-if="expandedDirs[c.path]?.loading" class="change-file__dirhint">加载中…</span>
                            <div v-for="f in (expandedDirs[c.path]?.files || [])" :key="f.path" class="change-file__row">
                              <span class="change-file__name">{{ f.path }}</span>
                              <ElButton size="small" text @click="previewChange(f)">预览</ElButton>
                              <a class="change-file__dl" :href="fileUrl(f.path)" target="_blank" rel="noopener">下载</a>
                            </div>
                          </div>
                        </template>
                        <template v-else>
                          <div class="change-file__row">
                            <span class="change-file__name">{{ c.path }}</span>
                            <template v-if="c.status !== 'deleted'">
                              <ElButton size="small" text @click="previewChange(c)">预览</ElButton>
                              <a class="change-file__dl" :href="fileUrl(c.path)" target="_blank" rel="noopener">下载</a>
                            </template>
                          </div>
                          <a
                            v-if="c.status !== 'deleted' && isImageFile(c.path)"
                            class="change-file__img" :href="fileUrl(c.path)" target="_blank" rel="noopener noreferrer"
                          >
                            <img :src="fileUrl(c.path)" :alt="c.path" />
                          </a>
                        </template>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </div>
        </template>
      </ElScrollbar>

      <!-- 输入区：统一圆角卡片（Claude 风格） -->
      <div class="ai-chat__composer">
        <CommandPalette :items="activePalette" :active-index="activeIndex" :prefix="mentionToken ? '@' : '/'" @select="acceptItem" />
        <div class="composer-inner">
          <div class="composer-card">
            <div v-if="attachments.length" class="composer-attachments">
              <span v-for="a in attachments" :key="a.path" class="attach-chip">
                <ElIcon><Document /></ElIcon>{{ a.name }}
                <ElIcon class="attach-chip__x" @click="store.removeAttachment(a.path)"><Close /></ElIcon>
              </span>
            </div>
            <ElInput
              ref="composerInputEl"
              v-model="input" type="textarea" :autosize="{ minRows: 1, maxRows: 8 }"
              class="composer-input"
              placeholder="给 AI 助手发消息…（Enter 发送，Shift+Enter 换行）"
              @keydown="onKey"
              @click="syncCursor"
              @keyup="syncCursor"
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
                      <ElDropdownItem command="memory" divided>我的记忆</ElDropdownItem>
                    </ElDropdownMenu>
                  </template>
                </ElDropdown>
              </div>
              <div class="composer-bar__right">
                <ElSelect
                  v-if="activeId"
                  v-model="composerAgent"
                  class="composer-agent"
                  size="small"
                  placeholder="默认 Agent"
                  clearable
                  filterable
                  :loading="agentsLoading"
                  @visible-change="(v) => v && !agents.length && fetchAgents()"
                >
                  <ElOption value="" label="默认 Agent" />
                  <ElOption
                    v-for="a in agents" :key="a.name"
                    :value="a.name" :label="a.name"
                  />
                </ElSelect>
                <ElSelect
                  v-if="activeId"
                  v-model="composerModel"
                  class="composer-model"
                  size="small"
                  placeholder="默认模型"
                  filterable
                  clearable
                  :loading="modelsLoading"
                  popper-class="composer-model__popper"
                  @visible-change="(v) => v && !models.length && fetchModels()"
                >
                  <ElOption value="" label="默认模型" />
                  <ElOption
                    v-for="m in models" :key="m.id"
                    :value="m.id" :label="m.label"
                  />
                </ElSelect>
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

    <!-- 变更文件 diff 预览面板 -->
    <ElDrawer v-model="diffOpen" :title="diffFile || '差异'" direction="rtl" size="60%">
      <div class="preview-body">
        <div v-if="diffLoading" class="ai-chat__pending"><ElIcon class="spin"><Loading /></ElIcon> 加载中…</div>
        <FileDiffView
          v-else-if="diffData"
          :status="diffData.status"
          :diff="diffData.diff"
          :content="diffData.content"
          :truncated="diffData.truncated"
          :filename="diffFile"
        />
        <a
          v-if="diffData && diffData.status !== 'deleted'"
          class="change-file__dl" :href="fileUrl(diffFile)" target="_blank" rel="noopener"
          style="display:inline-block;margin-top:12px"
        >下载完整文件</a>
      </div>
    </ElDrawer>

    <!-- 批任务对话框 -->
    <CreateBatchDialog
      v-model="showCreateBatch"
      @manageTemplates="showTemplateManager = true"
      @created="async (d) => { await batches.fetchList(); batches.selectBatch(d.batch.id) }" />
    <PromptTemplateManager v-model="showTemplateManager" />
    <MemoryManager v-model="showMemoryManager" />
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
  overflow: hidden;
}
.ai-sidebar__batches-head { display:flex; align-items:center; justify-content:space-between;
  margin: 10px 8px 4px; font-size: 12px; color: var(--el-text-color-secondary); font-weight: 600; }
.ai-sidebar__sessions-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
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
  &.is-closed { opacity: 0.55; }
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
.msg__meta {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-family: var(--el-font-family-mono, monospace);
}
.msg__actions {
  display: flex; gap: 4px;
  margin-top: 4px;
  opacity: 0;
  transition: opacity 0.15s ease;
  width: fit-content;
}
.msg__actions--assistant { margin-left: 0; }
.msg:hover .msg__actions { opacity: 0.9; }
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
  &__count { font-weight: 400; }
}
.output-group { margin-bottom: 2px; }
.output-group__head {
  display: flex; align-items: center; gap: 6px;
  width: 100%; padding: 4px 0; background: none; border: none; cursor: pointer;
  color: var(--el-text-color-regular);
}
.output-group__chev { transition: transform 0.15s; color: var(--el-text-color-secondary); &.open { transform: rotate(90deg); } }
.output-group__dir {
  flex: 1; text-align: left; font-size: 13px;
  font-family: var(--el-font-family-mono, monospace);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.output-group__count { font-size: 12px; color: var(--el-text-color-secondary); }
.output-group__body { padding-left: 4px; }
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
.composer-agent { width: 120px; }
.composer-model {
  width: 170px;
  :deep(.el-input__wrapper) {
    box-shadow: none;
    padding-left: 4px;
    padding-right: 4px;
  }
  :deep(.el-input__inner) {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    text-align: right;
  }
}
.composer-model__popper { max-width: 320px; }
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
.change-group { margin-bottom: 4px; }
.change-group__head {
  display: flex; align-items: center; gap: 6px;
  width: 100%; padding: 4px 0; background: none; border: none; cursor: pointer;
  color: var(--el-text-color-regular);
}
.change-group__chev { transition: transform 0.15s; color: var(--el-text-color-secondary); &.open { transform: rotate(90deg); } }
.change-group__count { font-size: 12px; color: var(--el-text-color-secondary); }
.change-group__body { padding-left: 4px; }
.change-file {
  display: flex; flex-direction: column; gap: 6px;
  padding: 6px 8px; border-radius: 6px; font-size: 14px;
  &:hover { background: var(--el-fill-color); }
  &__row { display: flex; align-items: center; gap: 8px; }
  &__name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--el-font-family-mono, monospace); }
  &__dl { color: var(--el-color-primary); text-decoration: none; font-size: 13px; }
  &__dircount { color: var(--el-text-color-secondary); font-size: 12px; white-space: nowrap; }
  &__dirhead {
    display: flex; align-items: center; gap: 8px; width: 100%;
    background: none; border: none; padding: 0; cursor: pointer; color: inherit; text-align: left;
  }
  &__chev { transition: transform 0.15s; &.open { transform: rotate(90deg); } }
  &__dirbody { padding-left: 18px; }
  &__dirhint { color: var(--el-text-color-secondary); font-size: 12px; }
  &__img {
    display: block;
    margin-left: 0;
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
