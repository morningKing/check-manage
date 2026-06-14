<template>
  <div v-if="availableTransitions.length > 0" class="workflow-actions">
    <el-button
      v-for="t in availableTransitions"
      :key="t.to"
      :type="getButtonType(t.to)"
      size="small"
      @click="handleTransition(t)"
      :loading="transitioning === t.to"
    >
      {{ t.label }}
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import type { FieldConfig, WorkflowTransition } from '@/types'
import { useAuthStore } from '@/stores'

const props = defineProps<{
  record: Record<string, any>
  fields: FieldConfig[]
}>()

const emit = defineEmits<{
  (e: 'transition', payload: { field: string; from: string; to: string }): void
}>()

const authStore = useAuthStore()
const transitioning = ref('')

const availableTransitions = computed(() => {
  const result: (WorkflowTransition & { fieldName: string })[] = []
  const userRole = authStore.userRole || 'guest'

  for (const field of props.fields) {
    const wf = field.workflowConfig
    if (!wf?.enabled || field.controlType !== 'select') continue

    const currentValue = props.record[field.fieldName] || ''

    for (const t of wf.transitions) {
      if (t.from !== '*' && t.from !== currentValue) continue
      if (t.roles && t.roles.length > 0 && !t.roles.includes(userRole)) continue
      result.push({ ...t, fieldName: field.fieldName })
    }
  }
  return result
})

function getButtonType(toStatus: string): string {
  const lower = toStatus.toLowerCase()
  if (lower.includes('done') || lower.includes('complete') || lower.includes('完成')) return 'success'
  if (lower.includes('reject') || lower.includes('cancel') || lower.includes('拒绝') || lower.includes('取消')) return 'danger'
  if (lower.includes('progress') || lower.includes('处理') || lower.includes('进行')) return 'warning'
  return 'primary'
}

function checkConditions(t: WorkflowTransition): string | null {
  if (!t.conditions?.length) return null
  for (const cond of t.conditions) {
    const val = props.record[cond.field]
    switch (cond.rule) {
      case 'notEmpty':
        if (val === null || val === undefined || val === '') return cond.message
        break
      case 'equals':
        if (val !== cond.value) return cond.message
        break
      case 'notEquals':
        if (val === cond.value) return cond.message
        break
    }
  }
  return null
}

async function handleTransition(t: WorkflowTransition & { fieldName: string }) {
  const error = checkConditions(t)
  if (error) {
    ElMessage.warning(error)
    return
  }

  // 确认由父组件（DynamicPage）的「推进/驳回意见」对话框承担，此处不再二次确认
  transitioning.value = t.to
  emit('transition', {
    field: t.fieldName,
    from: props.record[t.fieldName] || '',
    to: t.to,
  })
  // Parent handles the API call; reset loading after a short delay
  setTimeout(() => { transitioning.value = '' }, 2000)
}
</script>

<style scoped>
.workflow-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
