<template>
  <div v-if="open" class="se-find-panel" @mousedown.stop>
    <div class="se-find-row">
      <input
        ref="queryInput"
        v-model="query"
        class="se-find-input"
        placeholder="查找（Enter 下一个）"
        @keydown.esc.stop="close"
      />
      <span class="se-find-count">{{ countText }}</span>
      <button type="button" class="se-find-btn" title="上一个" :disabled="!matches.length" @click="step(-1)">‹</button>
      <button type="button" class="se-find-btn" title="下一个" :disabled="!matches.length" @click="step(1)">›</button>
      <button type="button" class="se-find-close" title="关闭 (Esc)" @click="close">×</button>
    </div>
    <div class="se-find-row">
      <input v-model="replacement" class="se-find-input" placeholder="替换为…" @keydown.esc.stop="close" />
      <label class="se-find-case">
        <input v-model="caseSensitive" type="checkbox" />区分大小写
      </label>
    </div>
    <div class="se-find-row">
      <button type="button" class="se-find-btn" :disabled="!matches.length" @click="replaceCurrent">替换</button>
      <button type="button" class="se-find-btn" :disabled="!matches.length" @click="replaceAllMatches">全部替换</button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 查找替换面板（Ctrl+F 唤起，编辑区右上角浮动；Word 式）：
 * - 命中计数 n/m + ‹› 跳转 + 当前命中高亮（core/find-replace 的装饰层）；
 * - 替换 / 全部替换各是一个事务，一次 Ctrl+Z 可整体还原；
 * - 打开时若编辑器有选区则预填为查找词；文档变化时自动重查（仅 doc 变化时，
 *   高亮 meta 事务不触发，避免自激循环）。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { Editor } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import {
  FIND_REPLACE_META,
  findMatches,
  replaceAll,
  replaceOne,
  scrollDocPosIntoView,
  type FindMatch,
} from './core/find-replace'

const props = defineProps<{ editor: Editor | null; open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const query = ref('')
const replacement = ref('')
const caseSensitive = ref(false)
const matches = ref<FindMatch[]>([])
const index = ref(-1)
const queryInput = ref<HTMLInputElement | null>(null)

/** 上次执行搜索时的文档引用：事务回调里据此判断「文档真的变了」 */
let lastSearchedDoc: PMNode | null = null

const countText = computed(() => {
  if (!query.value) return ''
  if (!matches.value.length) return '0 处'
  return `${index.value + 1}/${matches.value.length}`
})

function pushHighlight(): void {
  const editor = props.editor
  if (!editor) return
  const { view } = editor
  view.dispatch(
    view.state.tr.setMeta(FIND_REPLACE_META, { matches: matches.value, index: index.value }),
  )
}

function runSearch(keepIndex: boolean): void {
  const editor = props.editor
  if (!editor) return
  matches.value = findMatches(editor.state.doc, query.value, caseSensitive.value)
  lastSearchedDoc = editor.state.doc
  if (!matches.value.length) index.value = -1
  else if (!keepIndex || index.value < 0 || index.value >= matches.value.length) index.value = 0
  pushHighlight()
  if (index.value >= 0) scrollDocPosIntoView(editor, matches.value[index.value].from)
}

watch([query, caseSensitive], () => runSearch(false))

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    const editor = props.editor
    if (editor && !editor.state.selection.empty) {
      const { from, to } = editor.state.selection
      const text = editor.state.doc.textBetween(from, to, '\n')
      if (text && !text.includes('\n')) query.value = text
    }
    runSearch(false)
    await nextTick()
    queryInput.value?.focus()
    queryInput.value?.select()
  },
)

function onTransaction(): void {
  const editor = props.editor
  if (!editor || !props.open) return
  if (editor.state.doc !== lastSearchedDoc) runSearch(true)
}

watch(
  () => props.editor,
  (editor, previous) => {
    previous?.off('transaction', onTransaction)
    editor?.on('transaction', onTransaction)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  props.editor?.off('transaction', onTransaction)
})

function step(delta: number): void {
  if (!matches.value.length) return
  const len = matches.value.length
  index.value = (index.value + delta + len) % len
  pushHighlight()
  const editor = props.editor
  if (editor) scrollDocPosIntoView(editor, matches.value[index.value].from)
}

function replaceCurrent(): void {
  const editor = props.editor
  const match = matches.value[index.value]
  if (!editor || !match) return
  replaceOne(editor, match, replacement.value)
  // 文档变化由 onTransaction 重查；这里不动 index，替换后仍指向同一处
}

function replaceAllMatches(): void {
  const editor = props.editor
  if (!editor) return
  replaceAll(editor, matches.value, replacement.value)
  index.value = -1
}

function close(): void {
  emit('update:open', false)
  matches.value = []
  index.value = -1
  pushHighlight()
  props.editor?.commands.focus()
}
</script>
