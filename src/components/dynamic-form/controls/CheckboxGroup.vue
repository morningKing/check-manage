/**
 * 复选框组控件
 *
 * 职责：
 * - 渲染复选框组
 * - 支持静态选项配置
 * - 支持禁用状态
 */
<template>
  <el-checkbox-group v-model="checkboxValue" :disabled="field.disabled">
    <el-checkbox
      v-for="option in options"
      :key="String(option.value)"
      :value="option.value"
    >
      {{ option.label }}
    </el-checkbox>
  </el-checkbox-group>
</template>

<script setup lang="ts">
/**
 * CheckboxGroup 组件
 *
 * 基于 Element Plus CheckboxGroup 组件封装
 * 用于动态表单中的复选框
 */
import { computed } from 'vue'
import type { FieldConfig, FieldOption } from '@/types'

// ==================== Props & Emits ====================

interface Props {
  /** 字段配置 */
  field: FieldConfig
  /** 当前值（数组） */
  modelValue: Array<string | number | boolean> | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: Array<string | number | boolean>): void
}>()

// ==================== 计算属性 ====================

/**
 * 选项列表
 */
const options = computed<FieldOption[]>(() => {
  return props.field.options || []
})

/**
 * 双向绑定的值
 */
const checkboxValue = computed({
  get: () => props.modelValue || [],
  set: (value) => emit('update:modelValue', value)
})
</script>
