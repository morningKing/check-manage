/**
 * 日期选择控件
 *
 * 职责：
 * - 渲染日期选择器
 * - 支持日期和日期时间两种模式
 * - 支持 placeholder 和禁用状态
 */
<template>
  <el-date-picker
    v-model="dateValue"
    :type="pickerType"
    :placeholder="field.placeholder || '请选择日期'"
    :disabled="field.disabled"
    :value-format="valueFormat"
    clearable
    style="width: 100%"
  />
</template>

<script setup lang="ts">
/**
 * DatePicker 组件
 *
 * 基于 Element Plus DatePicker 组件封装
 * 用于动态表单中的日期/日期时间选择
 *
 * 根据 field.controlType 自动切换模式：
 * - 'date': 仅选择日期
 * - 'datetime': 选择日期和时间
 */
import { computed } from 'vue'
import type { FieldConfig } from '@/types'

// ==================== Props & Emits ====================

interface Props {
  /** 字段配置 */
  field: FieldConfig
  /** 当前值 */
  modelValue: string | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string | null): void
}>()

// ==================== 计算属性 ====================

/**
 * 选择器类型
 */
const pickerType = computed(() => {
  return props.field.controlType === 'datetime' ? 'datetime' : 'date'
})

/**
 * 值格式化字符串
 */
const valueFormat = computed(() => {
  return props.field.controlType === 'datetime'
    ? 'YYYY-MM-DD HH:mm:ss'
    : 'YYYY-MM-DD'
})

/**
 * 双向绑定的日期值
 */
const dateValue = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})
</script>
