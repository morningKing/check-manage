/**
 * 文本输入控件
 *
 * 职责：
 * - 渲染单行文本输入框
 * - 支持 placeholder 和禁用状态
 */
<template>
  <el-input
    v-model="inputValue"
    :placeholder="field.placeholder || '请输入'"
    :disabled="field.disabled"
    clearable
  />
</template>

<script setup lang="ts">
/**
 * TextInput 组件
 *
 * 基于 Element Plus Input 组件封装
 * 用于动态表单中的单行文本输入
 */
import { computed } from 'vue'
import type { FieldConfig } from '@/types'

// ==================== Props & Emits ====================

interface Props {
  /** 字段配置 */
  field: FieldConfig
  /** 当前值 */
  modelValue: string | number | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

// ==================== 计算属性 ====================

/**
 * 双向绑定的输入值
 */
const inputValue = computed({
  get: () => props.modelValue ?? '',
  set: (value) => emit('update:modelValue', value)
})
</script>
