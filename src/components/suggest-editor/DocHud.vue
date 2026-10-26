<template>
  <div class="se-doc-hud">
    <div v-if="outlineOpen" class="se-doc-hud-pop" @mousedown.stop>
      <div class="se-doc-hud-pop-title">目录</div>
      <div v-if="!headings.length" class="se-doc-hud-empty">
        暂无标题：用上方「段落格式」设 H1–H3 后可在此导航
      </div>
      <button
        v-for="(heading, i) in headings"
        :key="i"
        type="button"
        class="se-outline-item"
        :class="`is-h${heading.level}`"
        :title="heading.text"
        @click="goTo(heading)"
      >{{ heading.text || '（空标题）' }}</button>
    </div>
    <button
      type="button"
      class="se-doc-hud-btn"
      :class="{ 'is-open': outlineOpen }"
      aria-label="目录"
      title="目录：按文档标题快速跳转"
      @click="outlineOpen = !outlineOpen"
    >目录</button>
    <span class="se-doc-hud-count" :title="countTitle">{{ stats.words.toLocaleString() }} 字</span>
  </div>
</template>

<script setup lang="ts">
/**
 * 右下角文档 HUD：字数统计 + 目录导航（专注模式下顶栏隐藏，这里仍可见）。
 * 字数口径 = CJK 字符逐字 + 拉丁词（core/doc-stats）；目录抽 heading，点击滚动定位。
 * 统计/目录都从编辑器 update 事件防抖 250ms 刷新，长文档打字不卡顿。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Editor } from '@tiptap/core'
import { countDocument, extractHeadings, type DocHeading, type DocStats } from './core/doc-stats'
import { scrollDocPosIntoView } from './core/find-replace'

const props = defineProps<{ editor: Editor | null }>()

const stats = ref<DocStats>({ words: 0, chars: 0, paragraphs: 0 })
const headings = ref<DocHeading[]>([])
const outlineOpen = ref(false)

const countTitle = computed(
  () =>
    `字数 ${stats.value.words.toLocaleString()}（中文逐字 + 英文单词） · 字符（不含空白）${stats.value.chars.toLocaleString()} · 段落 ${stats.value.paragraphs}`,
)

let timer: ReturnType<typeof setTimeout> | null = null

function refresh(): void {
  const doc = props.editor?.state.doc
  if (!doc) return
  stats.value = countDocument(doc)
  headings.value = extractHeadings(doc)
}

function scheduleRefresh(): void {
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    timer = null
    refresh()
  }, 250)
}

watch(
  () => props.editor,
  (editor, previous) => {
    previous?.off('update', scheduleRefresh)
    if (editor) {
      editor.on('update', scheduleRefresh)
      refresh()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  props.editor?.off('update', scheduleRefresh)
  if (timer) clearTimeout(timer)
})

function goTo(heading: DocHeading): void {
  const editor = props.editor
  if (!editor) return
  scrollDocPosIntoView(editor, heading.pos)
}
</script>
