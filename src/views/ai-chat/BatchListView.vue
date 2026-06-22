<template>
  <div class="batch-list">
    <div class="batch-list__head">
      <ElButton type="primary" @click="emit('newBatch')">+ 新建批任务</ElButton>
    </div>
    <div class="batch-list__items">
      <div v-for="b in store.items" :key="b.id"
           class="batch-card"
           :class="{ active: store.activeBatch?.id === b.id }"
           @click="emit('select', b.id)">
        <div class="batch-card__name">{{ b.name }}</div>
        <ElProgress :percentage="percentOf(b)" :status="progressStatus(b)" />
        <div class="batch-card__meta">
          <span :class="`badge badge--${b.status}`">{{ statusLabel(b.status) }}</span>
          <span v-if="b.failed">· {{ b.failed }} 失败</span>
          <span>· {{ b.done }}/{{ b.total }}</span>
          <span class="muted">· {{ relativeTime(b.created_at) }}</span>
        </div>
        <div v-if="b.agent || b.model" class="batch-card__meta muted">
          <span v-if="b.agent">Agent: {{ b.agent }}</span>
          <span v-if="b.model">{{ b.agent ? ' · ' : '' }}模型: {{ b.model }}</span>
        </div>
      </div>
      <div v-if="!store.items.length" class="empty">还没有批任务</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { ElButton, ElProgress } from 'element-plus'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import type { AiChatBatch, BatchStatus } from '@/types/aiChatBatch'

const store = useAiChatBatchesStore()
const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'newBatch'): void
}>()

onMounted(async () => {
  await store.fetchList()
  store.startListPolling()
})
onUnmounted(() => store.stopListPolling())

function percentOf(b: AiChatBatch): number {
  return b.total === 0 ? 0 : Math.round(((b.done + b.failed) / b.total) * 100)
}

function progressStatus(b: AiChatBatch) {
  if (b.status === 'failed') return 'exception'
  if (b.status === 'completed') return 'success'
  if (b.status === 'partial') return 'warning'
  return undefined
}

function statusLabel(s: BatchStatus): string {
  return {
    pending: '等待中', running: '运行中', completed: '完成',
    partial: '部分完成', failed: '失败',
  }[s]
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const min = Math.floor(ms / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  return `${Math.floor(hr / 24)} 天前`
}
</script>

<style scoped lang="scss">
.batch-list { display: flex; flex-direction: column; height: 100%; }
.batch-list__head { padding: 12px; }
.batch-list__items { flex: 1; overflow: auto; padding: 0 8px 12px; }
.batch-card {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px; padding: 10px; margin-bottom: 8px; cursor: pointer;
  &.active { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
  &:hover { background: var(--el-fill-color-light); }
}
.batch-card__name { font-weight: 600; margin-bottom: 6px; }
.batch-card__meta { display: flex; gap: 6px; font-size: 12px; color: var(--el-text-color-secondary); margin-top: 6px; }
.badge { padding: 1px 6px; border-radius: 4px; font-size: 11px; }
.badge--running    { background: var(--el-color-primary-light-8); color: var(--el-color-primary); }
.badge--pending    { background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.badge--completed  { background: var(--el-color-success-light-8); color: var(--el-color-success); }
.badge--partial    { background: var(--el-color-warning-light-8); color: var(--el-color-warning); }
.badge--failed     { background: var(--el-color-danger-light-8); color: var(--el-color-danger); }
.muted { color: var(--el-text-color-placeholder); }
.empty { text-align: center; padding: 20px; color: var(--el-text-color-secondary); }
</style>
