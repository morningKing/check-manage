/**
 * 自动时间戳控件
 *
 * 职责：
 * - 只读展示格式化后的时间戳
 * - 无值时显示「自动生成」占位文本
 */
<template>
  <span class="auto-timestamp">{{ displayValue }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { FieldConfig } from '@/types'

interface Props {
  field: FieldConfig
  modelValue: string | null
}

const props = defineProps<Props>()

const displayValue = computed(() => {
  if (!props.modelValue) return '自动生成'
  try {
    const date = new Date(props.modelValue)
    if (isNaN(date.getTime())) return props.modelValue
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    const h = String(date.getHours()).padStart(2, '0')
    const min = String(date.getMinutes()).padStart(2, '0')
    const s = String(date.getSeconds()).padStart(2, '0')
    return `${y}-${m}-${d} ${h}:${min}:${s}`
  } catch {
    return props.modelValue
  }
})
</script>

<style scoped>
.auto-timestamp {
  color: #909399;
  font-size: 14px;
}
</style>
