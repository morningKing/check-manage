/**
 * 表单渲染器组件
 *
 * 职责：
 * - 根据字段配置动态渲染表单项
 * - 自动生成验证规则
 * - 管理表单数据
 *
 * 核心功能：
 * - 遍历字段配置，为每个字段渲染对应控件
 * - 根据 controlType 动态选择控件组件
 * - 自动处理双向数据绑定
 */
<template>
  <el-form
    ref="formRef"
    :model="formData"
    :rules="formRules"
    :label-width="labelWidth"
    :label-position="labelPosition"
    :disabled="disabled"
  >
    <!-- 遍历字段配置，渲染表单项 -->
    <el-form-item
      v-for="field in sortedFields"
      :key="field.id"
      :label="field.label"
      :prop="field.fieldName"
      :required="field.required"
      v-show="!field.hidden"
    >
      <!-- 动态渲染控件组件 -->
      <component
        :is="getControlComponent(field.controlType)"
        :field="field"
        :model-value="formData[field.fieldName]"
        @update:model-value="updateFieldValue(field.fieldName, $event)"
      />
    </el-form-item>
  </el-form>
</template>

<script setup lang="ts">
/**
 * FormRenderer 组件脚本
 *
 * Props：
 * - fields: 字段配置列表
 * - modelValue: 表单数据对象
 * - labelWidth: 标签宽度
 * - labelPosition: 标签位置
 * - disabled: 是否禁用表单
 *
 * Events：
 * - update:modelValue: 表单数据更新
 *
 * Expose：
 * - validate: 验证表单
 * - resetFields: 重置表单
 * - clearValidate: 清除验证
 */
import { ref, computed, watch } from 'vue'
import type { FormInstance } from 'element-plus'
import type { FieldConfig } from '@/types'
import { generateFormRules } from '@/utils/validation'
import { getControlComponent, getControlDefaultValue } from './controls'

// ==================== Props & Emits ====================

interface Props {
  /** 字段配置列表 */
  fields: FieldConfig[]
  /** 表单数据 */
  modelValue: Record<string, any>
  /** 标签宽度 */
  labelWidth?: string
  /** 标签位置 */
  labelPosition?: 'left' | 'right' | 'top'
  /** 是否禁用 */
  disabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  labelWidth: '120px',
  labelPosition: 'right',
  disabled: false
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, any>): void
}>()

// ==================== Refs ====================

/**
 * 表单实例引用
 */
const formRef = ref<FormInstance>()

// ==================== 计算属性 ====================

/**
 * 排序后的字段列表
 */
const sortedFields = computed(() => {
  return [...props.fields]
    .filter((f) => f.controlType !== 'autoTimestamp' && f.controlType !== 'autoSequence')
    .sort((a, b) => a.order - b.order)
})

/**
 * 表单数据（响应式）
 */
const formData = computed(() => props.modelValue)

/**
 * 表单验证规则
 *
 * 根据字段配置自动生成
 */
const formRules = computed(() => {
  return generateFormRules(props.fields)
})

// ==================== 方法 ====================

/**
 * 更新单个字段值
 *
 * @param fieldName - 字段名
 * @param value - 新值
 */
function updateFieldValue(fieldName: string, value: any): void {
  const newData = {
    ...props.modelValue,
    [fieldName]: value
  }
  emit('update:modelValue', newData)
}

/**
 * 验证表单
 *
 * @returns Promise<boolean> 验证结果
 */
async function validate(): Promise<boolean> {
  if (!formRef.value) return false
  try {
    await formRef.value.validate()
    return true
  } catch {
    return false
  }
}

/**
 * 重置表单
 */
function resetFields(): void {
  formRef.value?.resetFields()
}

/**
 * 清除验证状态
 */
function clearValidate(): void {
  formRef.value?.clearValidate()
}

/**
 * 初始化表单数据
 *
 * 为每个字段设置默认值
 */
function initFormData(): void {
  const data: Record<string, any> = {}
  props.fields.forEach((field) => {
    if (props.modelValue[field.fieldName] === undefined) {
      data[field.fieldName] =
        field.defaultValue ?? getControlDefaultValue(field.controlType)
    }
  })
  if (Object.keys(data).length > 0) {
    emit('update:modelValue', { ...props.modelValue, ...data })
  }
}

// ==================== 监听 ====================

/**
 * 监听字段配置变化，初始化表单数据
 */
watch(
  () => props.fields,
  () => {
    initFormData()
  },
  { immediate: true }
)

// ==================== 暴露方法 ====================

defineExpose({
  validate,
  resetFields,
  clearValidate,
  formRef
})
</script>
