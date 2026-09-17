<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElIcon } from 'element-plus'
import { Tools, Loading, CircleCheck, CircleClose, ArrowRight } from '@element-plus/icons-vue'
import { formatDuration } from '@/utils/aiMeta'
import { buildToolSummary, stringifyRedacted } from './tool-renderers/registry'

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
const bodyId = `tool-call-body-${Math.random().toString(36).slice(2, 9)}`

// P2 spec §5: the collapsed head shows a human-readable deterministic summary
// (action · result); raw input/result JSON stays available in the expandable
// detail section, with sensitive fields masked.
const summary = computed(() => buildToolSummary({
  name: props.name,
  normalizedName: '',
  title: props.title,
  input: props.input,
  result: props.result,
  status: props.status,
}))

const statusKind = computed(() => {
  if (props.status === 'completed') return 'ok'
  if (props.status === 'error') return 'err'
  if (props.status === 'cancelled') return 'cancelled'
  return 'run'
})

const inputText = computed(() =>
  summary.value.showRawInput ? stringifyRedacted(props.input, summary.value.sensitiveKeys) : '')
const resultText = computed(() => stringifyRedacted(props.result, summary.value.sensitiveKeys))

function toggle() { open.value = !open.value }
</script>

<template>
  <div class="tool-call" :class="`tool-call--${status || 'running'}`">
    <button
      type="button"
      class="tool-call__head"
      :aria-expanded="open"
      :aria-controls="bodyId"
      @click="toggle"
    >
      <ElIcon class="tool-call__chev" :class="{ open }"><ArrowRight /></ElIcon>
      <ElIcon class="tool-call__icon"><Tools /></ElIcon>
      <span class="tool-call__summary" :title="summary.actionText">{{ summary.actionText }}</span>
      <span v-if="summary.resultText" class="tool-call__result" :title="summary.resultText">{{ summary.resultText }}</span>
      <span v-if="durationText" class="tool-call__dur">{{ durationText }}</span>
      <span class="tool-call__status">
        <span class="tool-call__status-text">{{ summary.statusText }}</span>
        <ElIcon v-if="statusKind === 'ok'" class="ok"><CircleCheck /></ElIcon>
        <ElIcon v-else-if="statusKind === 'err'" class="err"><CircleClose /></ElIcon>
        <ElIcon v-else-if="statusKind === 'run'" class="run spin"><Loading /></ElIcon>
      </span>
    </button>
    <div v-show="open" :id="bodyId" class="tool-call__body">
      <div class="tool-call__sub">操作</div>
      <div class="tool-call__action">
        {{ summary.actionText }}<template v-if="summary.resultText"> —— {{ summary.resultText }}</template>
      </div>
      <template v-if="inputText">
        <div class="tool-call__sub">原始输入</div>
        <pre class="tool-call__code">{{ inputText }}</pre>
      </template>
      <template v-if="resultText">
        <div class="tool-call__sub">原始结果</div>
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
  width: 100%;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  &:hover { background: var(--el-fill-color-light); }
  &:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: -2px; }
}
.tool-call__chev { transition: transform 0.15s; color: var(--el-text-color-secondary); flex-shrink: 0; &.open { transform: rotate(90deg); } }
.tool-call__icon { color: var(--el-color-primary); flex-shrink: 0; }
.tool-call__summary {
  font-weight: 600; color: var(--el-text-color-primary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  min-width: 0; flex: 0 1 auto;
}
.tool-call__result {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  min-width: 0; flex: 1 1 auto;
  &::before { content: '· '; opacity: 0.6; }
}
.tool-call__dur {
  flex-shrink: 0; font-size: 12px; color: var(--el-text-color-secondary);
  font-family: var(--el-font-family-mono, monospace);
}
.tool-call__status {
  margin-left: auto; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 4px;
  .ok { color: var(--el-color-success); }
  .err { color: var(--el-color-danger); }
  .run { color: var(--el-color-primary); }
}
.tool-call__status-text { font-size: 12px; color: var(--el-text-color-secondary); }
.tool-call__body { padding: 4px 12px 12px; border-top: 1px solid var(--el-border-color-lighter); }
.tool-call__sub { font-size: 12px; color: var(--el-text-color-secondary); margin: 8px 0 4px; }
.tool-call__action { color: var(--el-text-color-regular); word-break: break-word; }
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
