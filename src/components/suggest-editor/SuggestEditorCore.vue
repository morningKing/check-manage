<template>
  <div class="se-scope">
    <div class="se-layout">
      <!-- 头部整体 sticky 兜底：正常布局下外层零溢出（正文只在下方区域内滚动），
           任何意外溢出时标题栏与工具栏也不会被滚走 -->
      <div class="se-head">
        <header class="se-topbar">
          <slot name="title" />
          <span class="se-badge" :class="`se-badge-${syncState}`">{{ syncStateLabel }}</span>
          <span class="se-status">{{ rewriteHint ?? statusText }}</span>
          <span v-if="connectionHint" class="se-conn-hint">⚠ {{ connectionHint }}</span>
          <button type="button" class="se-badge se-badge-toggle" @click="sidebarOpen = !sidebarOpen">
            {{ sidebarOpen ? '隐藏建议栏' : `建议 ${notes.ai.length + notes.local.length}` }}
          </button>
          <span class="se-hint">格式用上方工具栏 · 输入 / 插入块 · 停笔自动分析/联想 · Tab 接受</span>
          <slot name="actions" />
        </header>
        <EditorToolbar v-if="editor" :editor="editor" :with-sidebar-pad="sidebarOpen" />
      </div>
      <div class="se-workspace">
        <EditorContent v-if="editor" :editor="editor" class="se-editor-host" />
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
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 协作编辑器核心（自 suggest-editor 原型的 CollabEditor.tsx 移植到 Vue 3）：
 * - Y.Doc + HocuspocusProvider 直连 Node 服务（经 vite/proxy 的 /suggest-ws 隧道）；
 * - 三层建议（本地规则 / AI 分析 / 用户改写）经 decoration 呈现，接受才落正文；
 * - 模板初始化由父组件（SuggestEditorDocView）在 synced 后调用 setContent 完成。
 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, reactive, ref, shallowRef, watch } from 'vue'
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
import { COMMANDS_MAP, requestRewrite } from './core/commands'
import { CompletionGhost } from './core/completion'
import { SlashMenu } from './core/slash-menu'
import { createSuggestionLayers, SUGGESTIONS_REFRESH_META, type SuggestionItem } from './core/suggestions'
import { compileRules, type CompiledRules } from './core/check-rules'
import { getRules, type LocalCheckRule } from '@/api/suggestEditor'

const props = defineProps<{ docId: string; token: string }>()
const emit = defineEmits<{ (e: 'synced'): void }>()

/** 走同源反向代理（vite dev / proxy.py 均提供 /suggest-ws 的 WS 隧道），免去跨端口/CORS */
function wsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://'
  return `${protocol}${window.location.host}/suggest-ws`
}

const REWRITE_PRESETS = [
  { label: '美化文本', instruction: '在保持原意的前提下，让表达更优雅、流畅、书面化' },
  { label: '精简表达', instruction: '删掉冗余与空话，用更短的话表达同样的意思' },
  { label: '改得更正式', instruction: '改成正式、专业的书面语，适合对外文档' },
]

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
const myRewriteIds = new Set<string>()
const assistant = reactive<AssistantStatus>({})
const syncState = ref<'connecting' | 'synced' | 'disconnected'>('connecting')
/** 连接诊断提示（鉴权失败/重连中/重试耗尽），成功同步后清空 */
const connectionHint = ref<string | null>(null)
const menu = ref<ReviseMenu | null>(null)
const customInstruction = ref('')
const rewriteHint = ref<string | null>(null)
const notes = ref<{ ai: SuggestionItem[]; local: SuggestionItem[] }>({ ai: [], local: [] })
const activeNoteId = ref<string | null>(null)
const sidebarOpen = ref(true)
/** 全局 + 文档级检查规则（编译后）；null = 仅内置规则 */
const compiledRules = shallowRef<CompiledRules | null>(null)

const syncStateLabel = computed(() =>
  syncState.value === 'synced' ? '已同步' : syncState.value === 'connecting' ? '连接中' : '已断开',
)

const statusText = computed(() => {
  if (assistant.state === 'analyzing') return assistant.hint ? `正在分析 · ${assistant.hint}` : '正在分析…'
  if (assistant.state === 'error') return `分析失败：${assistant.error ?? '未知错误'}`
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

const editor = useEditor({
  extensions: [
    StarterKit.configure({ undoRedo: false }), // 协作模式用 Yjs 的 undo
    TaskList,
    TaskItem.configure({ nested: true }),
    Placeholder.configure({
      placeholder: '输入正文…（输入 / 唤起块菜单，格式用上方工具栏）',
    }),
    Collaboration.configure({ document: ydoc, field: 'default' }),
    suggestionLayer.extension,
    CompletionGhost({ ydoc, getEditor: () => editorRef.value }),
    SlashMenu,
  ],
  editorProps: {
    attributes: { class: 'doc-surface' },
    handleDOMEvents: {
      // 选中文本后右键：弹出改写菜单（未选中时保留系统菜单）
      contextmenu: (view, event) => {
        const { from, to } = view.state.selection
        if (from === to) return false
        const mouseEvent = event as MouseEvent
        mouseEvent.preventDefault()
        customInstruction.value = ''
        menu.value = {
          x: mouseEvent.clientX,
          y: mouseEvent.clientY,
          from,
          to,
          quote: view.state.doc.textBetween(from, to, '\n').slice(0, 80),
        }
        return true
      },
    },
  },
})

watch(
  editor,
  (value) => {
    editorRef.value = value ?? null
    // 调试钩子：?debug=1 时暴露 editor / ydoc（编辑器实例就绪即挂，供 E2E 与控制台排查）
    if (value && new URLSearchParams(window.location.search).has('debug')) {
      ;(window as unknown as Record<string, unknown>).__editor = value
      ;(window as unknown as Record<string, unknown>).__ydoc = ydoc
    }
  },
  { immediate: true },
)

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
      getRules({ kind: 'global' }).then((r) => r.rules).catch(() => [] as LocalCheckRule[]),
      getRules({ kind: 'document', docId: props.docId }).then((r) => r.rules).catch(() => [] as LocalCheckRule[]),
    ])
    // 文档级同 id 覆盖全局
    const byId = new Map<string, LocalCheckRule>()
    for (const rule of globalRules) byId.set(rule.id, rule)
    for (const rule of docRules) byId.set(rule.id, rule)
    compiledRules.value = compileRules([...byId.values()])
    refreshSuggestions()
  } catch (error) {
    console.warn('[suggest-editor] 检查规则加载失败，仅用内置规则：', error)
    compiledRules.value = null
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
})

onActivated(() => {
  if (!provider) {
    syncState.value = 'connecting'
    provider = createProvider()
  }
})

onBeforeUnmount(() => {
  releaseProvider()
  suggestionsMap.unobserveDeep(onSuggestionsChanged)
  assistantMap.unobserveDeep(() => readAssistantStatus())
  commandsMap.unobserve(onCommandsChanged)
  ydoc.destroy()
})

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
