<template>
  <span class="composite-text">{{ displayValue }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { FieldConfig } from '@/types'

interface Props {
  field: FieldConfig
  modelValue: string | null
  formData?: Record<string, any>
}

const props = defineProps<Props>()

const displayValue = computed(() => {
  const config = props.field.compositeTextConfig
  if (!config || !config.sourceFields?.length) return '未配置源字段'

  const values = config.sourceFields
    .map(fn => props.formData?.[fn])
    .filter(v => v !== null && v !== undefined && v !== '')
    .map(v => String(v))

  if (values.length === 0) return '-'
  return values.join(config.separator ?? ' - ')
})
</script>

<style scoped>
.composite-text {
  color: #606266;
  font-size: 14px;
}
</style>
