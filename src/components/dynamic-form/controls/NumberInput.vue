/**
 * 数字输入控件
 *
 * 职责：
 * - 渲染数字输入框
 * - 支持 placeholder 和禁用状态
 * - 支持精度控制
 */
<template>
  <el-input-number
    v-model="inputValue"
    :placeholder="field.placeholder || '请输入'"
    :disabled="field.disabled"
    :controls="true"
    controls-position="right"
    style="width: 100%"
  />
</template>

<script setup lang="ts">
/**
 * NumberInput 组件
 *
 * 基于 Element Plus InputNumber 组件封装
 * 用于动态表单中的数字输入
 */
import { computed } from 'vue'
import type { FieldConfig } from '@/types'

// ==================== Props & Emits ====================

interface Props {
  /** 字段配置 */
  field: FieldConfig
  /** 当前值 */
  modelValue: number | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: number | null): void
}>()

// ==================== 计算属性 ====================

/**
 * 双向绑定的输入值
 */
const inputValue = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})
</script>
