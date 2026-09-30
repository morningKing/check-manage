<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElButton, ElInput, ElOption, ElSelect, ElTag } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'

/**
 * 工具调用面板（agent_tool_calls 账本时间线，根会话 + 全部子代理）。
 * 调试信息——从 AI 会话页迁到管理页批任务详情（2026-09-30）；支持工具类型
 * 过滤与关键字搜索（匹配入参文本）。
 */
import type { AdminToolCall } from '@/api/aiBatchAdmin'

type Row = AdminToolCall & { id?: number }
const props = defineProps<{ calls: Row[]; loading?: boolean }>()
const emit = defineEmits<{ refresh: [] }>()

const toolFilter = ref('')
const keyword = ref('')
const expandedArgs = ref<number | null>(null)

const toolNames = computed(() =>
  [...new Set((props.calls || []).map(c => c.tool))].sort())

const rows = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const filtered = (props.calls || []).filter(c => {
    if (toolFilter.value && c.tool !== toolFilter.value) return false
    if (kw && !(`${c.args || ''}`.toLowerCase().includes(kw)
                || c.tool.toLowerCase().includes(kw)
                || (c.agent || '').toLowerCase().includes(kw))) return false
    return true
  })
  return filtered.slice().reverse()  // 账本按时间正序落库 → 展示最新在上
})

function stateLabel(state: string | null) {
  if (state === 'completed') return '完成'
  if (state === 'running') return '运行中'
  if (state === 'error') return '出错'
  return state || '—'
}
function singleLine(text: string | null) {
  return (text || '').replace(/\s+/g, ' ').slice(0, 120)
}
function timeOf(iso: string | null) {
  return iso ? iso.slice(5, 19).replace('T', ' ') : ''
}
function toggleArgs(id: number | undefined, index: number) {
  const key = id ?? index
  expandedArgs.value = expandedArgs.value === key ? null : key
}

watch(() => props.calls, () => { expandedArgs.value = null })
</script>

<template>
  <div class="ai-toolcalls" data-test="tool-calls-panel">
    <div class="ai-toolcalls__bar">
      <ElSelect v-model="toolFilter" size="small" clearable filterable
                placeholder="全部工具" style="width: 140px" data-test="tool-calls-filter">
        <ElOption v-for="t in toolNames" :key="t" :label="t" :value="t" />
      </ElSelect>
      <ElInput v-model="keyword" size="small" clearable placeholder="关键字搜索（入参/工具/子代理）"
               style="flex: 1" data-test="tool-calls-search" />
      <ElButton size="small" :icon="Refresh" :loading="loading" data-test="tool-calls-refresh"
                @click="emit('refresh')">刷新</ElButton>
    </div>
    <div v-if="!rows.length" class="ai-toolcalls__empty">
      暂无记录{{ (toolFilter || keyword) ? '（当前过滤条件下无匹配）' : '' }}
    </div>
    <div v-else class="ai-toolcalls__list">
      <div v-for="(c, i) in rows" :key="c.id ?? `${c.occurredAt}-${i}`" class="toolcall"
           :data-state="c.state || ''" @click="toggleArgs(c.id, i)">
        <div class="toolcall__row">
          <span class="toolcall__time">{{ timeOf(c.occurredAt) }}</span>
          <ElTag size="small" class="toolcall__tool">{{ c.tool }}</ElTag>
          <span class="toolcall__agent" :class="{ 'toolcall__agent--sub': !!(c.agent || c.subtaskId) }">
            {{ c.agent || (c.subtaskId ? '子代理' : '主会话') }}
          </span>
          <span class="toolcall__state">{{ stateLabel(c.state) }}</span>
        </div>
        <div v-if="expandedArgs === (c.id ?? i) && c.args" class="toolcall__args"
             @click.stop>{{ c.args }}</div>
        <div v-else-if="c.args" class="toolcall__args-preview">
          {{ singleLine(c.args) }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-toolcalls { display: flex; flex-direction: column; gap: 8px; }
.ai-toolcalls__bar { display: flex; gap: 8px; align-items: center; }
.ai-toolcalls__empty {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
  padding: 12px 0;
  text-align: center;
}
.ai-toolcalls__list { display: flex; flex-direction: column; gap: 6px; max-height: 60vh; overflow: auto; }
.toolcall {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 6px 8px;
  cursor: pointer;
}
.toolcall__row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.toolcall__time { color: var(--el-text-color-placeholder); font-variant-numeric: tabular-nums; }
.toolcall__agent { color: var(--el-text-color-secondary); }
.toolcall__agent--sub { color: var(--el-color-primary); }
.toolcall__state { margin-left: auto; color: var(--el-text-color-secondary); }
.toolcall[data-state='error'] .toolcall__state { color: var(--el-color-danger); }
.toolcall__args-preview {
  color: var(--el-text-color-placeholder);
  font-size: 11px;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.toolcall__args {
  margin-top: 4px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  padding: 6px;
  font-size: 11px;
  font-family: monospace;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow: auto;
}
</style>
