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
  /** 触发父组件重新加载表格数据；轮询期间用它拿到刷新后的行状态。
   * 缺省时退化为 no-op（轮询仍会走满 5 分钟盲等超时，但不报错）。 */
  onRefresh?: () => void | Promise<void>
  /** 按 id 在父组件已加载的表格数据里查找该行，用于判断是否已到终态。
   * 缺省时无法判断终态，轮询退回旧的"盲等 5 分钟"行为。 */
  getRowById?: (id: string) => Record<string, unknown> | undefined
}>()

/** 轮询间隔 5s，上限 5 分钟 —— AI 动作常跑几十秒到数分钟（兜底：拿不到
 * statusField/runningValue 或 getRowById 时用，正常情况下轮询会在行离开
 * runningValue 的那一刻提前停止，见 pollTick）。 */
const POLL_INTERVAL_MS = 5000
const POLL_MAX_MS = 5 * 60 * 1000

const paramDialogVisible = ref(false)
const pendingAction = ref<RowActionConfig | null>(null)
const pendingRow = ref<Record<string, unknown> | null>(null)
const paramFormRef = ref<InstanceType<typeof DynamicForm> | null>(null)
const submitting = ref(false)
const isPolling = ref(false)

interface PollTarget {
  recordId: string
  statusField: string
  runningValue: string
}

let pollTimer: ReturnType<typeof setInterval> | null = null
let pollStopTimer: ReturnType<typeof setTimeout> | null = null
let pollTarget: PollTarget | null = null

function stopPolling(notice = false) {
  if (pollTimer) clearInterval(pollTimer)
  if (pollStopTimer) clearTimeout(pollStopTimer)
  pollTimer = null
  pollStopTimer = null
  pollTarget = null
  const wasPolling = isPolling.value
  isPolling.value = false
  if (notice && wasPolling) {
    ElMessage.info('执行时间较长，请稍后手动刷新查看')
  }
}

async function pollTick() {
  await props.onRefresh?.()
  // 拿不到判断终态所需的信息（statusField 缺省 / 没接 getRowById）时，
  // 只能维持旧行为——刷新页面、盲等到 5 分钟上限。
  if (!pollTarget || !props.getRowById) return
  const row = props.getRowById(pollTarget.recordId)
  const current = row ? String(row[pollTarget.statusField] ?? '') : ''
  // 行已经离开 runningValue（成功/失败/或行被删除查不到了）：立即停止轮询，
  // 不等 5 分钟上限、也不弹"执行时间较长"的误导提示——它早就跑完了。
  if (current !== pollTarget.runningValue) {
    stopPolling(false)
  }
}

function startPolling(target: PollTarget | null) {
  stopPolling()
  pollTarget = target
  isPolling.value = true
  pollTimer = setInterval(pollTick, POLL_INTERVAL_MS)
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
    await props.onRefresh?.()
    if (res?.status === 'running') {
      const target = res.statusField
        ? { recordId: String(row.id), statusField: res.statusField, runningValue: res.runningValue ?? '' }
        : null
      startPolling(target)
    }
  } catch {
    // 非 2xx 响应已由全局请求拦截器统一弹出中文错误提示，这里不重复弹
  } finally {
    submitting.value = false
  }
}

onUnmounted(() => stopPolling())

defineExpose({ run, submitWithParams, isPolling, paramDialogVisible })
</script>
