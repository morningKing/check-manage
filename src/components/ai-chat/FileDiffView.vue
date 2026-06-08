<script setup lang="ts">
import { computed } from 'vue'
import { parseUnifiedDiff } from '@/utils/unifiedDiff'
import MarkdownView from './MarkdownView.vue'

const props = defineProps<{
  status: 'added' | 'modified' | 'deleted' | null
  diff?: string
  content?: string
  truncated?: boolean
  filename?: string
}>()

const hunks = computed(() => (props.status === 'modified' ? parseUnifiedDiff(props.diff || '') : []))

// A fence longer than the longest backtick run in the content, so content that
// itself contains ``` can't close the block early.
function safeFence(text: string): string {
  let longest = 0
  let run = 0
  for (const ch of text) {
    if (ch === '`') { run++; if (run > longest) longest = run }
    else run = 0
  }
  return '`'.repeat(Math.max(3, longest + 1))
}

// Language for highlighting = the file extension (from the basename, so a dotted
// directory + extension-less file doesn't misfire). Empty when there's no ext.
const addedMarkdown = computed(() => {
  if (props.status !== 'added') return ''
  const content = props.content || ''
  const base = (props.filename || '').split(/[\\/]/).pop() || ''
  const lang = base.includes('.') ? (base.split('.').pop() || '').toLowerCase() : ''
  const fence = safeFence(content)
  return `${fence}${lang}\n${content}\n${fence}`
})
</script>

<template>
  <div class="file-diff">
    <!-- modified: side-by-side hunks -->
    <template v-if="status === 'modified'">
      <div v-if="!hunks.length" class="diff-empty">无文本差异</div>
      <div v-for="(h, hi) in hunks" :key="hi" class="diff-hunk">
        <div class="diff-hunk__header">{{ h.header }}</div>
        <div v-for="(row, ri) in h.rows" :key="ri" class="diff-row" :class="`diff-row--${row.type}`">
          <span class="diff-no">{{ row.leftNo ?? '' }}</span>
          <span class="diff-cell" :class="{ 'diff-cell--del': row.type === 'del' || row.type === 'change' }">{{ row.left ?? '' }}</span>
          <span class="diff-no">{{ row.rightNo ?? '' }}</span>
          <span class="diff-cell" :class="{ 'diff-cell--add': row.type === 'add' || row.type === 'change' }">{{ row.right ?? '' }}</span>
        </div>
      </div>
    </template>

    <!-- added: render content as a highlighted code block -->
    <template v-else-if="status === 'added'">
      <MarkdownView :text="addedMarkdown" />
    </template>

    <div v-else-if="status === 'deleted'" class="diff-empty">该文件已删除，无可预览内容</div>
    <div v-else class="diff-empty">该文件当前没有差异（可能已被还原或移除）</div>

    <div v-if="truncated" class="diff-truncated">内容过大，已截断；请下载查看完整内容。</div>
  </div>
</template>

<style scoped lang="scss">
.file-diff { font-family: var(--el-font-family-mono, monospace); font-size: 12px; }
.diff-hunk { margin-bottom: 14px; border: 1px solid var(--el-border-color-light); border-radius: 6px; overflow: hidden; }
.diff-hunk__header { padding: 4px 10px; background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.diff-row { display: grid; grid-template-columns: 44px 1fr 44px 1fr; align-items: stretch; }
.diff-no { color: var(--el-text-color-secondary); text-align: right; padding: 0 6px; user-select: none; background: var(--el-fill-color-lighter); }
.diff-cell { padding: 0 8px; white-space: pre-wrap; word-break: break-word; }
.diff-cell--del { background: var(--el-color-danger-light-9); }
.diff-cell--add { background: var(--el-color-success-light-9); }
.diff-empty { color: var(--el-text-color-secondary); padding: 8px 0; }
.diff-truncated { margin-top: 10px; padding: 6px 10px; font-size: 12px; color: var(--el-color-warning-dark-2); background: var(--el-color-warning-light-9); border-radius: 6px; }
</style>
