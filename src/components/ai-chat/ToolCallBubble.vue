<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElIcon } from 'element-plus'
import { Tools, Loading, CircleCheck, CircleClose, ArrowRight } from '@element-plus/icons-vue'
import { formatDuration } from '@/utils/aiMeta'

const props = defineProps<{
  name: string
  title?: string
  status?: string
  input?: unknown
  result?: unknown
  durationMs?: number
}>()

const durationText = computed(() => formatDuration(props.durationMs))

const open = ref(false)

// Tool name (e.g. "bash", "read", "check-manage_list_collections") is the
// identity; OpenCode's `title` is usually a file path or argument preview.
// We show both so the user actually knows WHICH tool ran on WHAT.
// "check-manage_" namespace prefix is noise to the user — strip it.
const displayName = computed(() => (props.name || 'tool').replace(/^check-manage_/, ''))
const subtitle = computed(() => {
  const t = props.title?.trim()
  if (!t || t === props.name) return ''
  return t
})
const inputText = computed(() => fmt(props.input))
const resultText = computed(() => fmt(props.result))

function fmt(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}
</script>

<template>
  <div class="tool-call" :class="`tool-call--${status || 'running'}`">
    <div class="tool-call__head" @click="open = !open">
      <ElIcon class="tool-call__chev" :class="{ open }"><ArrowRight /></ElIcon>
      <ElIcon class="tool-call__icon"><Tools /></ElIcon>
      <span class="tool-call__name">{{ displayName }}</span>
      <span v-if="subtitle" class="tool-call__subtitle" :title="subtitle">{{ subtitle }}</span>
      <span v-if="durationText" class="tool-call__dur">{{ durationText }}</span>
      <span class="tool-call__status">
        <ElIcon v-if="status === 'completed'" class="ok"><CircleCheck /></ElIcon>
        <ElIcon v-else-if="status === 'error'" class="err"><CircleClose /></ElIcon>
        <ElIcon v-else class="run spin"><Loading /></ElIcon>
      </span>
    </div>
    <div v-show="open" class="tool-call__body">
      <template v-if="inputText">
        <div class="tool-call__sub">输入</div>
        <pre class="tool-call__code">{{ inputText }}</pre>
      </template>
      <template v-if="resultText">
        <div class="tool-call__sub">结果</div>
        <pre class="tool-call__code">{{ resultText }}</pre>
      </template>
    </div>
  </div>
</template>

<style scoped lang="scss">
.tool-call {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  margin: 8px 0;
  background: var(--el-fill-color-lighter);
  font-size: 13px;
  overflow: hidden;
}
.tool-call__head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  &:hover { background: var(--el-fill-color-light); }
}
.tool-call__chev { transition: transform 0.15s; color: var(--el-text-color-secondary); &.open { transform: rotate(90deg); } }
.tool-call__icon { color: var(--el-color-primary); flex-shrink: 0; }
.tool-call__name {
  font-weight: 600; color: var(--el-text-color-primary);
  font-family: var(--el-font-family-mono, monospace);
  flex-shrink: 0;
}
.tool-call__subtitle {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-family: var(--el-font-family-mono, monospace);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  flex: 1;
  &::before { content: '· '; opacity: 0.6; }
}
.tool-call__dur {
  flex-shrink: 0; font-size: 12px; color: var(--el-text-color-secondary);
  font-family: var(--el-font-family-mono, monospace);
}
.tool-call__status { margin-left: auto; flex-shrink: 0; .ok { color: var(--el-color-success); } .err { color: var(--el-color-danger); } .run { color: var(--el-color-primary); } }
.tool-call__body { padding: 4px 12px 12px; border-top: 1px solid var(--el-border-color-lighter); }
.tool-call__sub { font-size: 12px; color: var(--el-text-color-secondary); margin: 8px 0 4px; }
.tool-call__code {
  margin: 0;
  padding: 8px 10px;
  background: var(--el-fill-color-dark);
  border-radius: 6px;
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow: auto;
}
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
