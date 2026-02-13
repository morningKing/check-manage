/**
 * 单选按钮组控件
 *
 * 职责：
 * - 渲染单选按钮组
 * - 支持静态选项配置
 * - 支持禁用状态
 */
<template>
  <el-radio-group v-model="radioValue" :disabled="field.disabled">
    <el-radio
      v-for="option in options"
      :key="String(option.value)"
      :value="option.value"
    >
      {{ option.label }}
    </el-radio>
  </el-radio-group>
</template>

<script setup lang="ts">
/**
 * RadioGroup 组件
 *
 * 基于 Element Plus RadioGroup 组件封装
 * 用于动态表单中的单选按钮
 */
import { computed } from 'vue'
import type { FieldConfig, FieldOption } from '@/types'

// ==================== Props & Emits ====================

interface Props {
  /** 字段配置 */
  field: FieldConfig
  /** 当前值 */
  modelValue: string | number | boolean | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string | number | boolean | null): void
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
const radioValue = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})
</script>
