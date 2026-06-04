<script setup lang="ts">
import { computed } from 'vue'
import { parseUnifiedDiff } from '@/utils/unifiedDiff'

const props = defineProps<{
  status: 'added' | 'modified' | 'deleted' | null
  diff?: string
  content?: string
  truncated?: boolean
}>()

const hunks = computed(() => (props.status === 'modified' ? parseUnifiedDiff(props.diff || '') : []))
const addedLines = computed(() =>
  props.status === 'added' ? (props.content || '').replace(/\n$/, '').split('\n') : [],
)
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

    <!-- added: lightweight capped viewer -->
    <template v-else-if="status === 'added'">
      <pre class="diff-added-viewer"><span v-for="(ln, i) in addedLines" :key="i" class="diff-added-line"><span class="diff-no">{{ i + 1 }}</span>{{ ln }}</span></pre>
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
.diff-added-viewer { margin: 0; white-space: pre-wrap; word-break: break-word; }
.diff-added-line { display: block; }
.diff-added-line .diff-no { display: inline-block; width: 38px; margin-right: 10px; }
.diff-empty { color: var(--el-text-color-secondary); padding: 8px 0; }
.diff-truncated { margin-top: 10px; padding: 6px 10px; font-size: 12px; color: var(--el-color-warning-dark-2); background: var(--el-color-warning-light-9); border-radius: 6px; }
</style>
