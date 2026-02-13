/**
 * 多行文本输入控件
 *
 * 职责：
 * - 渲染多行文本输入框（textarea）
 * - 支持 placeholder 和禁用状态
 * - 可配置行数
 */
<template>
  <el-input
    v-model="inputValue"
    type="textarea"
    :placeholder="field.placeholder || '请输入'"
    :disabled="field.disabled"
    :rows="4"
    :autosize="{ minRows: 3, maxRows: 8 }"
  />
</template>

<script setup lang="ts">
/**
 * TextArea 组件
 *
 * 基于 Element Plus Input 组件的 textarea 模式封装
 * 用于动态表单中的多行文本输入
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
