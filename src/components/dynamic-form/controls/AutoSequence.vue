/**
 * 自增序列控件
 *
 * 职责：
 * - 只读展示自增序列编号
 * - 无值时显示「自动生成」占位文本
 */
<template>
  <span class="auto-sequence">{{ displayValue }}</span>
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
  if (!props.modelValue) return '保存后生成'
  return props.modelValue
})
</script>

<style scoped>
.auto-sequence {
  color: #909399;
  font-size: 14px;
}
</style>
