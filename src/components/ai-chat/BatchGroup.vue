<template>
  <div class="batch-group">
    <div class="batch-group__head" :class="{ open: expanded }" @click="toggle">
      <ElIcon class="caret"><ArrowRight v-if="!expanded" /><ArrowDown v-else /></ElIcon>
      <span class="bg-name">{{ batch.name }}</span>
      <span :class="`badge badge--${batch.status}`">{{ statusLabel(batch.status) }}</span>
      <span class="bg-meta">{{ batch.done }}/{{ batch.total }}</span>
      <span class="bg-am">{{ batch.agent || '默认' }} · {{ batch.model || '默认' }}</span>
      <span class="bg-actions" @click.stop>
        <ElIcon title="搜索本批任务" @click="emit('searchInBatch')"><Search /></ElIcon>
        <ElIcon title="编辑 Agent/模型" @click="editOpen = true"><Setting /></ElIcon>
        <ElIcon title="追加文件" @click="appendOpen = true"><Plus /></ElIcon>
        <ElIcon v-if="['pending', 'running'].includes(batch.status)" title="暂停全部（可继续）"
                @click="onPause"><VideoPause /></ElIcon>
        <ElIcon v-if="['pending', 'running', 'paused'].includes(batch.status)" title="中断全部（标记取消，之后可继续）"
                @click="onStop"><CircleClose /></ElIcon>
        <ElIcon v-if="batch.cancelled || batch.paused" title="继续运行（从暂停/中断处继续）"
                @click="onResume"><VideoPlay /></ElIcon>
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
              :title="(['failed', 'cancelled'].includes(s.status) && s.error_message) || s.last_message_preview || ''">
          {{ (['failed', 'cancelled'].includes(s.status) && s.error_message) ? s.error_message : (s.last_message_preview || '') }}
        </span>
        <ElIcon v-if="['pending', 'running'].includes(s.status)"
                class="bg-child__cancel" title="中断此任务"
                @click.stop="onCancel(s.id)"><VideoPause /></ElIcon>
        <ElIcon v-if="['completed', 'failed', 'cancelled'].includes(s.status)"
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
import { ArrowRight, ArrowDown, Plus, RefreshRight, RefreshLeft, Delete, Setting, VideoPause, VideoPlay, CircleClose, Search } from '@element-plus/icons-vue'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import { cancelChild } from '@/api/aiChatBatches'
import AppendFilesDialog from './AppendFilesDialog.vue'
import EditBatchConfigDialog from './EditBatchConfigDialog.vue'
import type { AiChatBatch } from '@/types/aiChatBatch'

const props = defineProps<{ batch: AiChatBatch; activeSessionId: string | null }>()
const emit = defineEmits<{ (e: 'selectChild', id: string): void; (e: 'searchInBatch'): void }>()
const store = useAiChatBatchesStore()
const appendOpen = ref(false)
const editOpen = ref(false)
const expanded = computed(() => store.activeBatch?.id === props.batch.id)

function toggle() {
  if (expanded.value) store.clearSelection()
  else store.selectBatch(props.batch.id)
}
function statusLabel(s: string) {
  return ({ pending: '待运行', running: '运行中', paused: '已暂停', completed: '已完成', partial: '部分失败', failed: '失败' } as Record<string, string>)[s] || s
}
function fileName(p?: string | null) { return (p || '').split('/').pop() || '' }
async function onRetry() { try { await store.retryFailed(props.batch.id) } catch { ElMessage.error('重试失败') } }
async function onStop() {
  try {
    await ElMessageBox.confirm(
      '中断全部子任务？排队中的不再执行，运行中的将被中断，已暂停的将转为取消。中断后可点「继续运行」在原工作上恢复。',
      '中断批任务', { type: 'warning' },
    )
    await store.stopBatch(props.batch.id)
    ElMessage.success('已请求中断')
    if (expanded.value) await store.selectBatch(props.batch.id)
  } catch (e: unknown) {
    if (e === 'cancel') return
    const err = e as { response?: { data?: { error?: string } } }
    ElMessage.error(err.response?.data?.error || '中断失败')
  }
}
async function onPause() {
  try {
    await ElMessageBox.confirm(
      '暂停全部子任务？运行中的回合会被中断并记为「已暂停」（不算失败），之后可点「继续运行」从原工作续跑。',
      '暂停批任务', { type: 'warning' },
    )
    await store.pauseBatch(props.batch.id)
    ElMessage.success('已请求暂停')
    if (expanded.value) await store.selectBatch(props.batch.id)
  } catch (e: unknown) {
    if (e === 'cancel') return
    const err = e as { response?: { data?: { error?: string } } }
    ElMessage.error(err.response?.data?.error || '暂停失败')
  }
}
async function onResume() {
  try {
    await store.resumeBatch(props.batch.id)
    ElMessage.success('已继续运行')
    if (!expanded.value) await store.selectBatch(props.batch.id)
  } catch (e: unknown) {
    const err = e as { response?: { data?: { error?: string } } }
    ElMessage.error(err.response?.data?.error || '继续运行失败')
  }
}
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
async function onCancel(sessionId: string) {
  try {
    await ElMessageBox.confirm(
      '确定中断此任务？中断后可在批次上点「继续运行」从中断处恢复。',
      '中断任务', { type: 'warning' },
    )
    await cancelChild(props.batch.id, sessionId)
    ElMessage.success('已请求中断')
    if (expanded.value) await store.selectBatch(props.batch.id)
  } catch (e: unknown) {
    if (e === 'cancel') return
    const err = e as { response?: { data?: { error?: string } } }
    ElMessage.error(err.response?.data?.error || '中断失败')
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
.dot--paused { background: var(--el-color-primary-light-3); }
.dot--cancelled { background: var(--el-text-color-secondary); }
.bg-child__cancel { cursor: pointer; flex: 0 0 auto; color: var(--el-text-color-secondary); }
.bg-child__cancel:hover { color: var(--el-color-danger); }
.bg-child__reexec { cursor: pointer; flex: 0 0 auto; color: var(--el-text-color-secondary); }
.bg-child__reexec:hover { color: var(--el-color-primary); }
.bg-empty { padding: 6px 8px; color: var(--el-text-color-secondary); font-size: 12px; }
.badge { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: var(--el-fill-color);
  color: var(--el-text-color-secondary); flex: 0 0 auto; }
.badge--running { background: var(--el-color-warning-light-8); color: var(--el-color-warning); }
.badge--paused { background: var(--el-color-primary-light-8); color: var(--el-color-primary); }
.badge--completed { background: var(--el-color-success-light-8); color: var(--el-color-success); }
.badge--failed { background: var(--el-color-danger-light-8); color: var(--el-color-danger); }
.badge--partial { background: var(--el-color-warning-light-8); color: var(--el-color-warning); }
.badge--pending { background: var(--el-fill-color); color: var(--el-text-color-secondary); }
</style>
