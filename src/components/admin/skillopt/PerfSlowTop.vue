<template>
  <ElCard shadow="never" class="perf-slow-top">
    <template #header>最慢任务 Top{{ tasks.length }}</template>
    <div v-if="!tasks.length" class="muted">暂无数据</div>
    <div v-for="t in tasks" :key="t.attemptId" class="row" @click="emit('open', t.attemptId)">
      <span class="def">{{ t.defName || t.sessionId.slice(0, 8) }}</span>
      <span class="wall">{{ fmtMs(t.wallMs) }}</span>
      <span class="muted">{{ t.sourceType }} · {{ t.startedAt || '' }}</span>
    </div>
  </ElCard>
</template>
<script setup lang="ts">
import type { PerfTaskEntry } from '@/api/aiSkills'
defineProps<{ tasks: PerfTaskEntry[] }>()
const emit = defineEmits<{ (e: 'open', attemptId: string): void }>()
function fmtMs(ms: number): string {
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`
}
</script>
<style scoped>
.row { display: flex; gap: 10px; padding: 6px 0; cursor: pointer; align-items: baseline; }
.row:hover { background: var(--el-fill-color-light); }
.def { font-weight: 600; min-width: 120px; }
.wall { font-variant-numeric: tabular-nums; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
