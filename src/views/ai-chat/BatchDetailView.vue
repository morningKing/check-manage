<template>
  <div v-if="batch" class="batch-detail">
    <div class="batch-detail__head">
      <div>
        <h3 class="title">{{ batch.name }}</h3>
        <div class="meta">
          <span :class="`badge badge--${batch.status}`">{{ statusLabel(batch.status) }}</span>
          <span>· {{ batch.done }} / {{ batch.total }}</span>
          <span v-if="batch.failed">· {{ batch.failed }} 失败</span>
          <span>· Agent: {{ batch.agent || '默认' }}</span>
          <span>· 模型: {{ batch.model || '默认' }}</span>
        </div>
      </div>
      <div class="actions">
        <ElButton v-if="batch.failed" type="warning" @click="onRetry">
          重试失败 ({{ batch.failed }})
        </ElButton>
        <ElButton @click="onDelete" :disabled="batch.status === 'running'">删除</ElButton>
      </div>
    </div>

    <div class="batch-detail__prompt">
      <span class="label">Prompt:</span>
      <span class="text" :title="batch.prompt">{{ truncated(batch.prompt) }}</span>
      <ElButton v-if="batch.prompt.length > 200" link size="small"
                @click="promptOpen = !promptOpen">
        {{ promptOpen ? '收起' : '展开' }}
      </ElButton>
      <div v-if="promptOpen" class="prompt-full">{{ batch.prompt }}</div>
    </div>

    <ElProgress :percentage="percent" :status="progressStatus" />

    <table class="sessions">
      <thead>
        <tr>
          <th>#</th><th>状态</th><th>文件</th><th>最近消息 / 错误</th><th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in sessions" :key="s.id">
          <td>{{ s.batch_seq + 1 }}</td>
          <td><span :class="`badge badge--${s.status}`">
            {{ sessionStatusLabel(s.status) }}
          </span></td>
          <td>{{ fileBaseName(s.batch_input_file) }}</td>
          <td>
            <span v-if="s.status === 'failed'" class="err">{{ s.error_message }}</span>
            <span v-else class="muted">{{ s.last_message_preview || '—' }}</span>
          </td>
          <td>
            <ElButton link size="small" :disabled="s.status === 'pending'"
                      @click="emit('openSession', s.id)">查看</ElButton>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div v-else class="empty">选择一个批任务查看详情</div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElProgress, ElMessageBox, ElMessage } from 'element-plus'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import type { BatchStatus, BatchSessionStatus } from '@/types/aiChatBatch'

const store = useAiChatBatchesStore()
const emit = defineEmits<{
  (e: 'openSession', id: string): void
}>()

const batch = computed(() => store.activeBatch)
const sessions = computed(() => store.activeSessions)
const promptOpen = ref(false)

const percent = computed(() => {
  const b = batch.value
  if (!b || b.total === 0) return 0
  return Math.round(((b.done + b.failed) / b.total) * 100)
})

const progressStatus = computed(() => {
  if (!batch.value) return undefined
  if (batch.value.status === 'completed') return 'success'
  if (batch.value.status === 'failed') return 'exception'
  if (batch.value.status === 'partial') return 'warning'
  return undefined
})

function truncated(s: string) { return s.length > 200 ? s.slice(0, 200) + '…' : s }
function fileBaseName(p: string) { return p.split('/').pop() || p }

function statusLabel(s: BatchStatus): string {
  return { pending: '等待中', running: '运行中', completed: '完成',
           partial: '部分完成', failed: '失败' }[s]
}
function sessionStatusLabel(s: BatchSessionStatus): string {
  return { pending: '排队', running: '运行中',
           completed: '完成', failed: '失败' }[s]
}

async function onRetry() {
  try {
    await store.retryFailed()
    ElMessage.success('已重新加入队列')
  } catch (e: any) {
    ElMessage.error(e?.message || '重试失败')
  }
}

async function onDelete() {
  if (!batch.value) return
  try {
    await ElMessageBox.confirm(
      `删除批任务「${batch.value.name}」会移除其下所有子会话。继续?`,
      '删除批任务', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  await store.removeBatch(batch.value.id)
}
</script>

<style scoped lang="scss">
.batch-detail { padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.batch-detail__head { display: flex; justify-content: space-between; align-items: flex-start; }
.title { margin: 0; font-size: 16px; }
.meta { font-size: 12px; color: var(--el-text-color-secondary); display: flex; gap: 6px; margin-top: 4px; }
.actions { display: flex; gap: 8px; }
.batch-detail__prompt { font-size: 13px; }
.batch-detail__prompt .label { color: var(--el-text-color-secondary); margin-right: 6px; }
.prompt-full { margin-top: 6px; padding: 8px 10px; background: var(--el-fill-color-light); border-radius: 6px; white-space: pre-wrap; }
.sessions { width: 100%; border-collapse: collapse; font-size: 13px; }
.sessions th, .sessions td { padding: 8px; border-bottom: 1px solid var(--el-border-color-lighter); text-align: left; }
.badge { padding: 1px 6px; border-radius: 4px; font-size: 11px; }
.badge--running    { background: var(--el-color-primary-light-8); color: var(--el-color-primary); }
.badge--pending    { background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.badge--completed  { background: var(--el-color-success-light-8); color: var(--el-color-success); }
.badge--partial    { background: var(--el-color-warning-light-8); color: var(--el-color-warning); }
.badge--failed     { background: var(--el-color-danger-light-8); color: var(--el-color-danger); }
.err { color: var(--el-color-danger); }
.muted { color: var(--el-text-color-secondary); }
.empty { padding: 40px; text-align: center; color: var(--el-text-color-secondary); }
</style>
