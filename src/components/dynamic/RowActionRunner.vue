<template>
  <el-dialog
    v-model="paramDialogVisible"
    :title="pendingAction?.label || '执行操作'"
    width="520px"
    append-to-body
  >
    <DynamicForm
      v-if="pendingAction?.paramFields?.length"
      ref="paramFormRef"
      :fields="pendingAction.paramFields"
      :collection="collection"
      :show-actions="false"
    />
    <template #footer>
      <el-button @click="paramDialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submitWithParams">
        确定
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 行操作执行器
 *
 * 负责：二次确认 → 参数表单 → 提交 → 轮询该行状态直至终态。
 * 挂在 DynamicPage 上，由行「⋯」菜单里的按钮调用 run()。
 */
import { ref, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import DynamicForm from '@/components/dynamic-form/DynamicForm.vue'
import { runRowAction } from '@/api/rowAction'
import type { RowActionConfig } from '@/types/rowAction'

const props = defineProps<{
  collection: string
}>()

const emit = defineEmits<{
  (e: 'refresh'): void
}>()

/** 轮询间隔 5s，上限 5 分钟 —— AI 动作常跑几十秒到数分钟 */
const POLL_INTERVAL_MS = 5000
const POLL_MAX_MS = 5 * 60 * 1000

const paramDialogVisible = ref(false)
const pendingAction = ref<RowActionConfig | null>(null)
const pendingRow = ref<Record<string, unknown> | null>(null)
const paramFormRef = ref<InstanceType<typeof DynamicForm> | null>(null)
const submitting = ref(false)
const isPolling = ref(false)

let pollTimer: ReturnType<typeof setInterval> | null = null
let pollStopTimer: ReturnType<typeof setTimeout> | null = null

function stopPolling(notice = false) {
  if (pollTimer) clearInterval(pollTimer)
  if (pollStopTimer) clearTimeout(pollStopTimer)
  pollTimer = null
  pollStopTimer = null
  const wasPolling = isPolling.value
  isPolling.value = false
  if (notice && wasPolling) {
    ElMessage.info('执行时间较长，请稍后手动刷新查看')
  }
}

function startPolling() {
  stopPolling()
  isPolling.value = true
  pollTimer = setInterval(() => emit('refresh'), POLL_INTERVAL_MS)
  pollStopTimer = setTimeout(() => stopPolling(true), POLL_MAX_MS)
}

async function run(action: RowActionConfig, row: Record<string, unknown>) {
  if (action.confirmText) {
    try {
      await ElMessageBox.confirm(action.confirmText, action.label, {
        type: 'warning',
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }

  pendingAction.value = action
  pendingRow.value = row

  if (action.paramFields?.length) {
    paramDialogVisible.value = true
    return
  }
  await submit({})
}

async function submitWithParams() {
  const valid = await paramFormRef.value?.validate()
  if (!valid) return
  const params = paramFormRef.value?.getFormData() ?? {}
  await submit(params)
}

async function submit(params: Record<string, unknown>) {
  const action = pendingAction.value
  const row = pendingRow.value
  if (!action || !row) return

  submitting.value = true
  try {
    const res = await runRowAction(
      props.collection,
      String(row.id),
      action.id,
      params,
    )
    paramDialogVisible.value = false
    ElMessage.success('已提交')
    emit('refresh')
    if (res?.status === 'running') startPolling()
  } catch {
    // 非 2xx 响应已由全局请求拦截器统一弹出中文错误提示，这里不重复弹
  } finally {
    submitting.value = false
  }
}

onUnmounted(() => stopPolling())

defineExpose({ run, submitWithParams, isPolling, paramDialogVisible })
</script>
