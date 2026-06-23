<template>
  <div class="batch-group">
    <div class="batch-group__head" :class="{ open: expanded }" @click="toggle">
      <ElIcon class="caret"><ArrowRight v-if="!expanded" /><ArrowDown v-else /></ElIcon>
      <span class="bg-name">{{ batch.name }}</span>
      <span :class="`badge badge--${batch.status}`">{{ statusLabel(batch.status) }}</span>
      <span class="bg-meta">{{ batch.done }}/{{ batch.total }}</span>
      <span class="bg-am">{{ batch.agent || '默认' }} · {{ batch.model || '默认' }}</span>
      <span class="bg-actions" @click.stop>
        <ElIcon title="编辑 Agent/模型" @click="editOpen = true"><Setting /></ElIcon>
        <ElIcon title="追加文件" @click="appendOpen = true"><Plus /></ElIcon>
        <ElIcon v-if="batch.failed" title="重试失败" @click="onRetry"><RefreshRight /></ElIcon>
        <ElIcon title="删除批次" @click="onDelete"><Delete /></ElIcon>
      </span>
    </div>
    <div v-if="expanded" class="batch-group__body">
      <div v-for="s in store.activeSessions" :key="s.id"
           class="bg-child" :class="{ active: s.id === activeSessionId }"
           @click="$emit('selectChild', s.id)">
        <span :class="`dot dot--${s.status}`" />
        <span class="bg-child__file">{{ fileName(s.batch_input_file) }}</span>
        <span class="bg-child__preview"
              :class="{ 'is-error': s.status === 'failed' && !!s.error_message }"
              :title="(s.status === 'failed' && s.error_message) || s.last_message_preview || ''">
          {{ (s.status === 'failed' && s.error_message) ? s.error_message : (s.last_message_preview || '') }}
        </span>
        <ElIcon v-if="s.status === 'completed' || s.status === 'failed'"
                class="bg-child__reexec" title="重新执行（清空上下文）"
                @click.stop="onReexec(s.id)"><RefreshLeft /></ElIcon>
      </div>
      <div v-if="!store.activeSessions.length" class="bg-empty">加载中…</div>
    </div>
    <AppendFilesDialog v-model="appendOpen" :batch-id="batch.id" @appended="onAppended" />
    <EditBatchConfigDialog v-model="editOpen" :batch="batch" @saved="onConfigSaved" />
  </div>
</template>
<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElIcon, ElMessageBox, ElMessage } from 'element-plus'
import { ArrowRight, ArrowDown, Plus, RefreshRight, RefreshLeft, Delete, Setting } from '@element-plus/icons-vue'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import AppendFilesDialog from './AppendFilesDialog.vue'
import EditBatchConfigDialog from './EditBatchConfigDialog.vue'
import type { AiChatBatch } from '@/types/aiChatBatch'

const props = defineProps<{ batch: AiChatBatch; activeSessionId: string | null }>()
defineEmits<{ (e: 'selectChild', id: string): void }>()
const store = useAiChatBatchesStore()
const appendOpen = ref(false)
const editOpen = ref(false)
const expanded = computed(() => store.activeBatch?.id === props.batch.id)

function toggle() {
  if (expanded.value) store.clearSelection()
  else store.selectBatch(props.batch.id)
}
function statusLabel(s: string) {
  return ({ pending: '待运行', running: '运行中', completed: '已完成', partial: '部分失败', failed: '失败' } as Record<string, string>)[s] || s
}
function fileName(p?: string | null) { return (p || '').split('/').pop() || '' }
async function onRetry() { try { await store.retryFailed(props.batch.id) } catch { ElMessage.error('重试失败') } }
async function onDelete() {
  try {
    await ElMessageBox.confirm('删除该批次及其所有子任务？', '删除', { type: 'warning' })
    await store.removeBatch(props.batch.id)
  } catch { /* cancelled */ }
}
async function onReexec(sessionId: string) {
  try { await store.reexecuteChild(props.batch.id, sessionId) }
  catch (e: unknown) {
    const err = e as { response?: { data?: { error?: string } } }
    ElMessage.error(err.response?.data?.error || '重新执行失败')
  }
}
async function onAppended() { if (expanded.value) await store.selectBatch(props.batch.id) }
async function onConfigSaved() { if (expanded.value) await store.selectBatch(props.batch.id) }
</script>
<style scoped>
.batch-group__head { display: flex; align-items: center; gap: 6px; padding: 6px 8px;
  cursor: pointer; border-radius: 6px; font-size: 13px; flex-wrap: wrap; }
.batch-group__head:hover { background: var(--el-fill-color-light); }
.bg-name { font-weight: 600; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bg-meta, .bg-am { color: var(--el-text-color-secondary); font-size: 11px; }
.bg-am { flex-basis: 100%; padding-left: 22px; }
.bg-actions { display: flex; gap: 6px; }
.bg-actions .el-icon { cursor: pointer; }
.batch-group__body { padding-left: 18px; }
.bg-child { display: flex; align-items: center; gap: 6px; padding: 5px 8px; cursor: pointer;
  border-radius: 6px; font-size: 12px; }
.bg-child:hover, .bg-child.active { background: var(--el-fill-color); }
.bg-child__file { flex: 0 0 auto; max-width: 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bg-child__preview { color: var(--el-text-color-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bg-child__preview.is-error { color: var(--el-color-danger); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--el-color-info); flex: 0 0 auto; }
.dot--completed { background: var(--el-color-success); }
.dot--failed { background: var(--el-color-danger); }
.dot--running { background: var(--el-color-warning); }
.bg-child__reexec { cursor: pointer; flex: 0 0 auto; color: var(--el-text-color-secondary); }
.bg-child__reexec:hover { color: var(--el-color-primary); }
.bg-empty { padding: 6px 8px; color: var(--el-text-color-secondary); font-size: 12px; }
.badge { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: var(--el-fill-color);
  color: var(--el-text-color-secondary); flex: 0 0 auto; }
.badge--running { background: var(--el-color-warning-light-8); color: var(--el-color-warning); }
.badge--completed { background: var(--el-color-success-light-8); color: var(--el-color-success); }
.badge--failed { background: var(--el-color-danger-light-8); color: var(--el-color-danger); }
.badge--partial { background: var(--el-color-warning-light-8); color: var(--el-color-warning); }
.badge--pending { background: var(--el-fill-color); color: var(--el-text-color-secondary); }
</style>
