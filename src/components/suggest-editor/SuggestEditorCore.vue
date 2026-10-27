<template>
  <div class="se-scope" :class="{ 'is-focus': focusMode }">
    <div class="se-layout">
      <!-- 头部整体 sticky 兜底：正常布局下外层零溢出（正文只在下方区域内滚动），
           任何意外溢出时标题栏与工具栏也不会被滚走。
           顶栏与格式工具合并为单行：状态收敛为状态药丸、低频格式收「⋯」、删提示文案 -->
      <div class="se-head">
        <header class="se-topbar">
          <slot name="title" />
          <EditorToolbar
            v-if="editor"
            ref="toolbarRef"
            :editor="editor"
            :doc-id="docId"
            @ai-rewrite="openReviseMenuAtSelection"
            @open-shortcuts="shortcutsOpen = true"
            @print="onPrint"
          />
          <span class="se-status-pill">
            <i class="se-status-dot" :class="`is-${syncState}`" />
            <span class="se-badge" :class="`se-badge-${syncState}`">{{ syncStateLabel }}</span>
            <span
              class="se-rules-badge"
              :class="{ 'is-error': loadedRuleCount === null }"
              :title="loadedRuleCount === null
                ? '检查规则加载失败：当前仅内置规则生效'
                : `已加载 ${loadedRuleCount} 条检查规则（本地毫秒级实时检查）`"
            >规则 {{ loadedRuleCount ?? '?' }}</span>
          </span>
          <span v-if="connectionHint" class="se-conn-hint" :title="connectionHint">⚠ {{ connectionHint }}</span>
          <span class="se-status" :title="rewriteHint ?? statusText">{{ rewriteHint ?? statusText }}</span>
          <button type="button" class="se-badge se-badge-toggle" @click="sidebarOpen = !sidebarOpen">
            {{ sidebarOpen ? '收起' : `建议 ${notes.ai.length + notes.local.length}` }}
          </button>
          <slot name="actions" />
          <button
            type="button"
            class="se-focus-btn"
            :aria-label="focusMode ? '退出专注' : '专注模式'"
            :title="focusMode ? '退出专注 (Esc)' : '专注模式：隐藏工具栏与页签，正文满屏'"
            @click="toggleFocus"
          >⛶</button>
        </header>
      </div>
      <div class="se-workspace">
        <EditorContent v-if="editor" :editor="editor" class="se-editor-host" />
        <FindReplacePanel v-model:open="findOpen" :editor="editor ?? null" />
        <SuggestionSidebar
          v-if="sidebarOpen"
          :ai="notes.ai"
          :local="notes.local"
          :active-id="activeNoteId"
          @accept="onAccept"
          @ignore="onIgnore"
          @focus="onFocus"
          @hover="onHover"
        />
      </div>
      <DocHud :editor="editor ?? null" />
      <div
        v-if="menu"
        class="se-revise-menu"
        :style="{ left: `${menu.x}px`, top: `${menu.y}px` }"
        @mousedown.stop
      >
        <div class="se-revise-menu-quote">“{{ menu.quote }}”</div>
        <div class="se-revise-menu-row">
          <button
            v-for="preset in REWRITE_PRESETS"
            :key="preset.label"
            type="button"
            class="se-revise-menu-btn"
            @click="submitRewrite(preset.instruction)"
          >
            {{ preset.label }}
          </button>
        </div>
        <div class="se-revise-menu-row se-revise-tone">
          <span class="se-revise-tone-label">语气</span>
          <input
            v-model.number="toneValue"
            class="se-revise-tone-slider"
            type="range"
            min="0"
            max="4"
            step="1"
            aria-label="语气（口语到正式）"
            title="口语 ↔ 正式，选好后点「按语气改写」"
          />
          <span class="se-revise-tone-value">{{ TONE_LABELS[toneValue] ?? '中性' }}</span>
          <button type="button" class="se-revise-menu-btn" @click="submitRewrite(toneInstruction)">
            按语气改写
          </button>
        </div>
        <div class="se-revise-menu-row">
          <input
            v-model="customInstruction"
            class="se-revise-menu-input"
            placeholder="用一句话描述你想怎么改…"
            @keydown.enter="submitRewrite(customInstruction)"
          />
          <button type="button" class="se-revise-menu-btn se-revise-menu-send" @click="submitRewrite(customInstruction)">
            发送
          </button>
        </div>
      </div>
      <el-dialog v-model="shortcutsOpen" title="键盘快捷键" width="440px" append-to-body>
        <table class="se-shortcuts">
          <tbody>
            <tr v-for="row in SHORTCUT_ROWS" :key="row.keys">
              <td>{{ row.keys }}</td>
              <td>{{ row.desc }}</td>
            </tr>
          </tbody>
        </table>
      </el-dialog>
    </div>
    <!-- 专注模式下的浮动退出钮（顶栏整体隐藏后唯一常驻出口；Esc 同效） -->
    <button
      v-if="focusMode"
      type="button"
      class="se-focus-exit"
      title="退出专注 (Esc)"
      @click="exitFocus"
    >退出专注 (Esc)</button>
  </div>
</template>

<script setup lang="ts">
/**
 * 协作编辑器核心（自 suggest-editor 原型的 CollabEditor.tsx 移植到 Vue 3）：
 * - Y.Doc + HocuspocusProvider 直连 Node 服务（经 vite/proxy 的 /suggest-ws 隧道）；
 * - 三层建议（本地规则 / AI 分析 / 用户改写）经 decoration 呈现，接受才落正文；
 * - 模板初始化由父组件（SuggestEditorDocView）在 synced 后调用 setContent 完成。
 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { EditorContent, useEditor } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import Collaboration from '@tiptap/extension-collaboration'
import { TaskItem, TaskList } from '@tiptap/extension-list'
import { Placeholder } from '@tiptap/extensions'
import { HocuspocusProvider } from '@hocuspocus/provider'
import * as Y from 'yjs'
import type { Editor } from '@tiptap/core'
import EditorToolbar from './EditorToolbar.vue'
import SuggestionSidebar from './SuggestionSidebar.vue'
import DocHud from './DocHud.vue'
import FindReplacePanel from './FindReplacePanel.vue'
import { COMMANDS_MAP, requestRewrite } from './core/commands'
import { CompletionGhost } from './core/completion'
import { createSlashMenu } from './core/slash-menu'
import { createSuggestionLayers, SUGGESTIONS_REFRESH_META, type SuggestionItem } from './core/suggestions'
import { compileRules, type CompiledRules } from './core/check-rules'
import { FORMATTING_EXTENSIONS } from './core/formatting'
import { TABLE_EXTENSIONS } from './core/table'
import { FIGURE_EXTENSIONS, insertFigure } from './core/figure'
import { FindReplaceHighlight } from './core/find-replace'
import { getRules, getSnippets, uploadAttachment, type LocalCheckRule, type TextSnippet } from '@/api/suggestEditor'
import { useAppStore } from '@/stores/app'

const props = defineProps<{ docId: string; token: string }>()
const emit = defineEmits<{ (e: 'synced'): void }>()

/** 专注模式：全局 UI 态（AppLayout 据此隐藏平台头部，ContentArea 隐藏页签） */
const appStore = useAppStore()
const focusMode = computed(() => appStore.focusMode)
let sidebarWasOpen = true

function enterFocus(): void {
  sidebarWasOpen = sidebarOpen.value
  appStore.setFocusMode(true)
  sidebarOpen.value = false
}

function exitFocus(): void {
  if (!appStore.focusMode) return
  appStore.setFocusMode(false)
  sidebarOpen.value = sidebarWasOpen
}

function toggleFocus(): void {
  if (focusMode.value) exitFocus()
  else enterFocus()
}

function onEscKey(event: KeyboardEvent): void {
  if (event.key === 'Escape') exitFocus()
}

watch(focusMode, (active) => {
  if (active) window.addEventListener('keydown', onEscKey)
  else window.removeEventListener('keydown', onEscKey)
})

onMounted(() => {
  if (focusMode.value) window.addEventListener('keydown', onEscKey)
})

/** 全局快捷键（capture）：Ctrl+F 查找替换 / Ctrl+/ 快捷键帮助 / Alt+A AI 改写选中文字 */
function onGlobalKeydown(event: KeyboardEvent): void {
  const key = event.key
  const target = event.target as Element | null
  if ((event.ctrlKey || event.metaKey) && (key === 'f' || key === 'F')) {
    // 对话框里的输入框不劫持（规则/词表管理等面板有自己的输入场景）
    if (target?.closest?.('.el-dialog')) return
    event.preventDefault()
    findOpen.value = true
    return
  }
  if ((event.ctrlKey || event.metaKey) && (key === '/' || key === '?')) {
    if (target?.closest?.('.el-dialog')) return
    event.preventDefault()
    shortcutsOpen.value = !shortcutsOpen.value
    return
  }
  if (event.altKey && (key === 'a' || key === 'A')) {
    if (!editorRef.value) return
    const { from, to } = editorRef.value.state.selection
    if (from === to) return
    event.preventDefault()
    openReviseMenuAtSelection()
    return
  }
  if (key === 'Escape') {
    if (findOpen.value) {
      findOpen.value = false
      return
    }
    if (shortcutsOpen.value) {
      shortcutsOpen.value = false
      return
    }
    if (focusMode.value) exitFocus()
  }
}

/** 点击改写菜单外部关闭（此前菜单只能提交才能关掉，是既有易用性缺口） */
function onDocMouseDown(event: MouseEvent): void {
  if (!menu.value) return
  if ((event.target as Element | null)?.closest?.('.se-revise-menu')) return
  menu.value = null
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown)
  document.addEventListener('mousedown', onDocMouseDown)
})

/** 走同源反向代理（vite dev / proxy.py 均提供 /suggest-ws 的 WS 隧道），免去跨端口/CORS */
function wsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://'
  return `${protocol}${window.location.host}/suggest-ws`
}

/** 改写预设（对齐市面智能写作的划词动作面：润色/篇幅/语气/语病/翻译） */
const REWRITE_PRESETS = [
  { label: '美化文本', instruction: '在保持原意的前提下，让表达更优雅、流畅、书面化' },
  { label: '改得更正式', instruction: '改成正式、专业的书面语，适合对外文档' },
  { label: '改得口语化', instruction: '改成通俗易懂、口语化的表达，让非专业读者也能读懂' },
  { label: '精简表达', instruction: '删掉冗余与空话，用更短的话表达同样的意思' },
  { label: '扩写', instruction: '在保持原意与事实的前提下，把这段内容写得更充实、具体，补充必要的细节与说明' },
  { label: '纠正语病', instruction: '找出并纠正这段话里的语病、错别字与搭配不当，保持原意，输出修正后的文本' },
  { label: '翻译成英文', instruction: '把这段话翻译成地道、专业的英文' },
]

/** 快捷键帮助对话框内容（Ctrl+/ 或「⋯」菜单打开） */
const SHORTCUT_ROWS = [
  { keys: 'Ctrl+B / I / U', desc: '加粗 / 斜体 / 下划线' },
  { keys: 'Ctrl+Z / Ctrl+Y', desc: '撤销 / 重做' },
  { keys: '/', desc: '句首唤起块菜单（正文、标题、列表、表格…）' },
  { keys: 'Tab', desc: '接受补全候选或续写联想；Esc 丢弃' },
  { keys: 'Ctrl+Space', desc: '强制弹出补全候选（词中也可）' },
  { keys: 'Ctrl+J', desc: 'AI 续写一段（从光标处接着写）' },
  { keys: '右键 / Alt+A', desc: 'AI 改写选中文字（美化、扩写、精简、翻译…）' },
  { keys: 'Ctrl+F', desc: '查找与替换' },
  { keys: 'Ctrl+/', desc: '本帮助' },
  { keys: 'Esc', desc: '关闭弹层 / 退出专注模式' },
]

/** 打印 / 导出 PDF：走浏览器打印（print CSS 已隐藏编辑器 chrome，建议标注不进纸面） */
function onPrint(): void {
  window.print()
}

/** 语气滑杆（口语 ↔ 正式，5 档）：提交为一条语气改写指令 */
const TONE_LABELS = ['很口语', '偏口语', '中性', '偏正式', '很正式']
const toneValue = ref(2)
const toneInstruction = computed(
  () => `把语气调整为「${TONE_LABELS[toneValue.value] ?? '中性'}」（程度 ${toneValue.value + 1}/5），保持原意与事实不变`,
)

interface AssistantStatus {
  state?: string
  hint?: string
  error?: string
  lastApplied?: number
}

interface ReviseMenu {
  x: number
  y: number
  from: number
  to: number
  quote: string
}

const ydoc = new Y.Doc()

const editorRef = shallowRef<Editor | null>(null)
const toolbarRef = ref<InstanceType<typeof EditorToolbar> | null>(null)
const myRewriteIds = new Set<string>()
const assistant = reactive<AssistantStatus>({})
const syncState = ref<'connecting' | 'synced' | 'disconnected'>('connecting')
/** 连接诊断提示（鉴权失败/重连中/重试耗尽），成功同步后清空 */
const connectionHint = ref<string | null>(null)
const menu = ref<ReviseMenu | null>(null)
const customInstruction = ref('')
const rewriteHint = ref<string | null>(null)
/** 查找替换面板 / 快捷键帮助（Ctrl+F / Ctrl+/） */
const findOpen = ref(false)
const shortcutsOpen = ref(false)
const notes = ref<{ ai: SuggestionItem[]; local: SuggestionItem[] }>({ ai: [], local: [] })
const activeNoteId = ref<string | null>(null)
const sidebarOpen = ref(true)
/** 全局 + 文档级检查规则（编译后）；null = 仅内置规则 */
const compiledRules = shallowRef<CompiledRules | null>(null)
/** 已加载的检查规则条数（null=加载失败）；顶栏小徽标让「规则是否生效」一眼可见 */
const loadedRuleCount = ref<number | null>(null)
/** 后台可配置的文本模板（/ 菜单里的快捷填充项） */
const snippets = shallowRef<TextSnippet[]>([])

const syncStateLabel = computed(() =>
  syncState.value === 'synced' ? '已同步' : syncState.value === 'connecting' ? '连接中' : '已断开',
)

const statusText = computed(() => {
  if (assistant.state === 'analyzing') return assistant.hint ? `正在分析 · ${assistant.hint}` : '正在分析…'
  if (assistant.state === 'error') return `分析失败：${assistant.error ?? '未知错误'}`
  // 导入基线/块级缓存等空闲态提示也要可见，否则用户只看到“待分析”
  if (assistant.hint) return assistant.hint
  if (typeof assistant.lastApplied === 'number') return `上次分析新增 ${assistant.lastApplied} 条建议`
  return '待分析'
})

// Word 式批注层：正文只画标注线 + 编号标记，卡片交给右侧栏；标记与卡片互相联动
const suggestionLayer = createSuggestionLayers({
  ydoc,
  getEditor: () => editorRef.value,
  onChange: (items) => {
    notes.value = items
  },
  onActiveChange: (id) => {
    activeNoteId.value = id
  },
  getCheckRules: () => compiledRules.value,
})
void loadRules()
void loadSnippets()

/**
 * 规则/文本模板变更跨标签页通知：管理面保存/导入后广播，已打开的编辑器即时重载
 * 本地规则与模板（否则要等下一次打开文档，标签页内的旧数据会让人误以为"编辑没生效"）。
 */
let rulesChannel: BroadcastChannel | null = null
if (typeof BroadcastChannel !== 'undefined') {
  rulesChannel = new BroadcastChannel('suggest-editor-rules')
  rulesChannel.onmessage = () => {
    void loadRules()
    void loadSnippets()
  }
}

const editor = useEditor({
  extensions: [
    StarterKit.configure({ undoRedo: false }), // 协作模式用 Yjs 的 undo
    TaskList,
    TaskItem.configure({ nested: true }),
    Placeholder.configure({
      placeholder: '输入正文…（输入 / 唤起块菜单，格式用上方工具栏）',
    }),
    ...FORMATTING_EXTENSIONS,
    ...TABLE_EXTENSIONS,
    ...FIGURE_EXTENSIONS,
    Collaboration.configure({ document: ydoc, field: 'default' }),
    suggestionLayer.extension,
    FindReplaceHighlight(),
    CompletionGhost({ ydoc, getEditor: () => editorRef.value }),
    createSlashMenu(() => snippets.value),
  ],
  editorProps: {
    attributes: { class: 'doc-surface' },
    handlePaste: (_view, event) => {
      const files = Array.from(event.clipboardData?.files ?? [])
        .filter((file) => file.type.startsWith('image/'))
      if (!files.length) return false
      event.preventDefault()
      void insertUploadedImages(files)
      return true
    },
    handleDrop: (_view, event, _slice, moved) => {
      if (moved) return false
      const files = Array.from(event.dataTransfer?.files ?? [])
        .filter((file) => file.type.startsWith('image/'))
      if (!files.length) return false
      event.preventDefault()
      void insertUploadedImages(files)
      return true
    },
    handleDOMEvents: {
      // 选中文本后右键：弹出改写菜单（未选中时保留系统菜单）
      contextmenu: (view, event) => {
        const { from, to } = view.state.selection
        if (from === to) return false
        const mouseEvent = event as MouseEvent
        mouseEvent.preventDefault()
        openReviseMenu(from, to, { x: mouseEvent.clientX, y: mouseEvent.clientY })
        return true
      },
    },
  },
})

watch(
  editor,
  (value, previous) => {
    previous?.view.dom.removeEventListener('se:insert-image', onInsertImageEvent)
    value?.view.dom.addEventListener('se:insert-image', onInsertImageEvent)
    editorRef.value = value ?? null
    // 调试钩子：?debug=1 时暴露 editor / ydoc（编辑器实例就绪即挂，供 E2E 与控制台排查）
    if (value && new URLSearchParams(window.location.search).has('debug')) {
      ;(window as unknown as Record<string, unknown>).__editor = value
      ;(window as unknown as Record<string, unknown>).__ydoc = ydoc
    }
  },
  { immediate: true },
)

function onInsertImageEvent(): void {
  toolbarRef.value?.openImagePicker()
}

async function insertUploadedImages(files: File[]): Promise<void> {
  for (const file of files) {
    try {
      const { attachment } = await uploadAttachment(props.docId, file)
      if (!editorRef.value) return
      insertFigure(editorRef.value, { src: attachment.url, alt: file.name })
    } catch {
      // 错误提示由请求拦截器统一处理；继续上传其余图片
    }
  }
}

function refreshSuggestions(): void {
  const view = editorRef.value?.view
  if (view) view.dispatch(view.state.tr.setMeta(SUGGESTIONS_REFRESH_META, true))
}

/**
 * 拉取全局 + 文档级检查规则并编译；文档级同 id 覆盖全局，单份失败只降级该份。
 * 编辑器打开时调用一次；规则对话框保存成功后经 expose 的 reloadRules 重载。
 */
async function loadRules(): Promise<void> {
  try {
    const [globalRules, docRules] = await Promise.all([
      getRules({ kind: 'global' }, { silent: true }).then((r) => r.rules).catch(() => [] as LocalCheckRule[]),
      getRules({ kind: 'document', docId: props.docId }, { silent: true })
        .then((r) => r.rules)
        .catch(() => [] as LocalCheckRule[]),
    ])
    // 文档级同 id 覆盖全局
    const byId = new Map<string, LocalCheckRule>()
    for (const rule of globalRules) byId.set(rule.id, rule)
    for (const rule of docRules) byId.set(rule.id, rule)
    const merged = [...byId.values()]
    compiledRules.value = compileRules(merged)
    loadedRuleCount.value = merged.filter((rule) => rule.enabled !== false).length
    refreshSuggestions()
  } catch (error) {
    console.warn('[suggest-editor] 检查规则加载失败，仅用内置规则：', error)
    compiledRules.value = null
    loadedRuleCount.value = null
  }
}

/** 文本模板只影响 / 菜单内容，加载失败时静默保留上一份（首次失败则只有内置块类型） */
async function loadSnippets(): Promise<void> {
  try {
    const result = await getSnippets({ silent: true })
    snippets.value = result.snippets
  } catch (error) {
    console.warn('[suggest-editor] 文本模板加载失败，/ 菜单仅保留内置块类型：', error)
  }
}

function readAssistantStatus(): void {
  const assistantMap = ydoc.getMap('assistant')
  assistantMap.forEach((value, key) => {
    (assistant as unknown as Record<string, unknown>)[key] = value
  })
}

const suggestionsMap = ydoc.getMap('suggestions')
const assistantMap = ydoc.getMap('assistant')
const commandsMap = ydoc.getMap(COMMANDS_MAP)

const onSuggestionsChanged = () => refreshSuggestions()
const onCommandsChanged = () => {
  let hint: string | null = null
  for (const id of myRewriteIds) {
    const value = commandsMap.get(id) as { kind?: unknown; status?: unknown; error?: unknown } | undefined
    if (!value || value.kind !== 'rewrite') continue
    if (value.status === 'pending' || value.status === 'running') hint = '改写中…'
    else if (value.status === 'error') hint = `改写失败：${String(value.error ?? '未知错误')}`
    else if (value.status === 'done') hint = '改写完成，见文档中的卡片'
  }
  rewriteHint.value = hint
}
const onSynced = () => {
  syncState.value = 'synced'
  connectionHint.value = null
  readAssistantStatus()
  refreshSuggestions()
  emit('synced')
}
const onDisconnect = (payload?: { event?: CloseEvent }) => {
  syncState.value = 'disconnected'
  const code = payload?.event?.code
  // 1000=正常关闭；1006=异常断开（隧道/服务端断链）；其余为协议级关闭
  connectionHint.value =
    code === 1000 ? null : `连接已断开（${code ? `code ${code}` : '无码'}），自动重连中…`
  console.warn(`[suggest-editor] WS 断开 doc=${props.docId} code=${code ?? '-'}，provider 将自动重连`)
}
const onConnect = () => {
  syncState.value = 'connecting'
  console.info(`[suggest-editor] WS 建立 doc=${props.docId}（等待同步）`)
}
/** 服务端 onAuthenticate 拒绝时带回具体原因（令牌过期/签名不匹配/非属主…） */
const onAuthenticationFailed = (payload: { reason?: string }) => {
  const reason = payload?.reason || '未知原因'
  syncState.value = 'disconnected'
  connectionHint.value = `鉴权失败：${reason}`
  console.error(`[suggest-editor] 鉴权失败 doc=${props.docId}：${reason}——若是令牌过期请退出重新登录`)
}
const onMaxAttemptsFailed = (payload: { error?: unknown }) => {
  syncState.value = 'disconnected'
  connectionHint.value = '连接失败（已达最大重试次数）：检查服务是否运行、网络/反向代理是否放行 WebSocket'
  console.error(`[suggest-editor] 连接重试耗尽 doc=${props.docId}：`, payload?.error)
}

suggestionsMap.observeDeep(onSuggestionsChanged)
assistantMap.observeDeep(() => readAssistantStatus())
commandsMap.observe(onCommandsChanged)

/**
 * Provider 生命周期：ContentArea 对所有页面套 keep-alive，切走时组件不会卸载。
 * onDeactivated 断开 WS（服务端随即落盘并回收该文档的 agent 进程），
 * onActivated 重新建连——同一 Y.Doc 重连后由服务端最新状态回灌（断线重连语义）。
 * 连接诊断：console 一律 [suggest-editor] 前缀 + 顶栏提示具体失败原因
 * （与服务端 proxy [WS] 行、proxy-se.log [ws] 行三处按时间对齐排障）。
 */
let provider: HocuspocusProvider | null = null

function createProvider(): HocuspocusProvider {
  const url = wsUrl()
  console.info(`[suggest-editor] 发起连接 ${url} doc=${props.docId}`)
  const p = new HocuspocusProvider({ url, name: props.docId, document: ydoc, token: props.token })
  p.on('synced', onSynced)
  p.on('disconnect', onDisconnect)
  p.on('connect', onConnect)
  p.on('authenticationFailed', onAuthenticationFailed)
  p.on('maxAttemptsFailed', onMaxAttemptsFailed)
  return p
}

function releaseProvider(): void {
  if (!provider) return
  provider.off('synced', onSynced)
  provider.off('disconnect', onDisconnect)
  provider.off('connect', onConnect)
  provider.off('authenticationFailed', onAuthenticationFailed)
  provider.off('maxAttemptsFailed', onMaxAttemptsFailed)
  provider.destroy()
  provider = null
}

provider = createProvider()

onDeactivated(() => {
  releaseProvider()
  syncState.value = 'disconnected'
  // keep-alive 切走时必须退出专注：否则回列表页仍顶着全屏态（平台头部被藏）
  exitFocus()
})

onActivated(() => {
  if (!provider) {
    syncState.value = 'connecting'
    provider = createProvider()
  }
})

onBeforeUnmount(() => {
  releaseProvider()
  rulesChannel?.close()
  rulesChannel = null
  window.removeEventListener('keydown', onEscKey)
  window.removeEventListener('keydown', onGlobalKeydown)
  document.removeEventListener('mousedown', onDocMouseDown)
  if (appStore.focusMode) appStore.setFocusMode(false)
  suggestionsMap.unobserveDeep(onSuggestionsChanged)
  assistantMap.unobserveDeep(() => readAssistantStatus())
  commandsMap.unobserve(onCommandsChanged)
  ydoc.destroy()
})

/** 改写菜单定位收拢到视口内（菜单宽约 380px、高最多约 270px） */
function clampMenuX(x: number): number {
  return Math.max(8, Math.min(x, window.innerWidth - 388))
}
function clampMenuY(y: number): number {
  return Math.max(8, Math.min(y, window.innerHeight - 280))
}

/**
 * 打开改写菜单。有 anchor（右键坐标）时贴锚点；否则锚到选区末尾下方
 * （顶栏按钮 / Alt+A 发起时用选区坐标，而不是按钮坐标——动作对象是选中的文字）。
 */
function openReviseMenu(from: number, to: number, anchor?: { x: number; y: number }): void {
  const current = editorRef.value
  if (!current) return
  customInstruction.value = ''
  toneValue.value = 2
  const quote = current.state.doc.textBetween(from, to, '\n').slice(0, 80)
  if (anchor) {
    menu.value = { x: clampMenuX(anchor.x), y: clampMenuY(anchor.y), from, to, quote }
    return
  }
  const start = current.view.coordsAtPos(from)
  const end = current.view.coordsAtPos(to)
  menu.value = {
    x: clampMenuX(Math.min(start.left, end.left)),
    y: clampMenuY(Math.max(start.bottom, end.bottom) + 6),
    from,
    to,
    quote,
  }
}

/** 顶栏「AI 改写」按钮 / Alt+A：对当前选区打开改写菜单 */
function openReviseMenuAtSelection(): void {
  const current = editorRef.value
  if (!current) return
  const { from, to } = current.state.selection
  if (from === to) return
  openReviseMenu(from, to)
}

function submitRewrite(instruction: string): void {
  const current = editorRef.value
  if (!current || !menu.value) return
  const text = instruction.trim()
  if (!text) return
  const id = requestRewrite({
    ydoc,
    editor: current,
    instruction: text,
    from: menu.value.from,
    to: menu.value.to,
  })
  if (id) myRewriteIds.add(id)
  menu.value = null
  customInstruction.value = ''
  rewriteHint.value = '改写中…'
}

function onAccept(item: SuggestionItem): void {
  suggestionLayer.api.accept(item)
}
function onIgnore(item: SuggestionItem): void {
  suggestionLayer.api.ignore(item)
}
function onFocus(item: SuggestionItem): void {
  suggestionLayer.api.focus(item)
}
function onHover(id: string | null): void {
  activeNoteId.value = id
  suggestionLayer.api.setActive(id)
}

defineExpose({
  /** refs 经 expose 自动解包：父组件拿到 Editor 实例（或 undefined） */
  editor,
  ydoc,
  /** 规则保存后由父组件调用：重新拉取 + 重算本地命中 */
  reloadRules: loadRules,
})
</script>

<style lang="scss">
@import './editor.scss';
</style>
