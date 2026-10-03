<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { computed } from 'vue'

const props = defineProps<{ data: { label: string; kind: string; status: string; error: string | null } }>()

const color = computed(() => {
  const map: Record<string, string> = {
    succeeded: '#67c23a', failed: '#f56c6c', running: '#409eff',
    waiting_approval: '#e6a23c', blocked: '#909399', skipped: '#c0c4cc', needs_review: '#9b59b6',
  }
  return map[props.data.status] ?? '#909399'
})
const badge = computed(() => {
  const map: Record<string, string> = {
    succeeded: '✓', failed: '✗', running: '●',
    waiting_approval: '⏳', blocked: '○', skipped: '→', needs_review: '?',
  }
  return map[props.data.status] ?? '○'
})
</script>

<template>
  <div class="orch-step-node" :style="{ borderColor: color }" :data-status="data.status">
    <Handle type="target" :position="Position.Top" />
    <div class="orch-step-node__badge" :style="{ background: color }">{{ badge }}</div>
    <div class="orch-step-node__label">{{ data.label }}</div>
    <div class="orch-step-node__kind">{{ data.kind }}</div>
    <Handle type="source" :position="Position.Bottom" />
  </div>
</template>

<style scoped>
.orch-step-node {
  border: 2px solid #909399; border-radius: 8px; padding: 8px 12px;
  background: #fff; min-width: 140px; cursor: pointer;
  font-size: 12px; position: relative;
}
.orch-step-node__badge {
  position: absolute; top: -8px; right: -8px; width: 20px; height: 20px;
  border-radius: 50%; display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 11px; font-weight: bold;
}
.orch-step-node__label { font-weight: 500; }
.orch-step-node__kind { color: #909399; font-size: 10px; }
.orch-step-node[data-status='running'] { animation: orch-pulse 1.5s infinite; }
@keyframes orch-pulse { 50% { box-shadow: 0 0 8px rgba(64,158,255,0.5); } }
</style>
