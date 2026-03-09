/**
 * 动态表单组件
 *
 * 职责：
 * - 封装 FormRenderer，提供完整的表单功能
 * - 支持新增和编辑两种模式
 * - 集成表单提交和取消逻辑
 *
 * 使用场景：
 * - 数据新增对话框
 * - 数据编辑对话框
 * - 独立表单页面
 */
<template>
  <div class="dynamic-form">
    <!-- 表单渲染器 -->
    <FormRenderer
      ref="formRendererRef"
      :fields="fields"
      v-model="formData"
      :label-width="labelWidth"
      :label-position="labelPosition"
      :disabled="loading"
    />

    <!-- 表单操作按钮 -->
    <div v-if="showActions" class="form-actions">
      <el-button @click="handleCancel" :disabled="loading">
        取消
      </el-button>
      <el-button type="primary" @click="handleSubmit" :loading="loading">
        {{ submitText }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * DynamicForm 组件脚本
 *
 * Props：
 * - fields: 字段配置列表
 * - initialData: 初始数据（编辑模式）
 * - labelWidth: 标签宽度
 * - labelPosition: 标签位置
 * - showActions: 是否显示操作按钮
 * - submitText: 提交按钮文本
 *
 * Events：
 * - submit: 表单提交
 * - cancel: 取消操作
 */
import { ref, computed, watch } from 'vue'
import type { FieldConfig } from '@/types'
import FormRenderer from './FormRenderer.vue'
import { getControlDefaultValue } from './controls'

// ==================== Props & Emits ====================

interface Props {
  /** 字段配置列表 */
  fields: FieldConfig[]
  /** 初始数据 */
  initialData?: Record<string, any>
  /** 标签宽度 */
  labelWidth?: string
  /** 标签位置 */
  labelPosition?: 'left' | 'right' | 'top'
  /** 是否显示操作按钮 */
  showActions?: boolean
  /** 提交按钮文本 */
  submitText?: string
}

const props = withDefaults(defineProps<Props>(), {
  initialData: () => ({}),
  labelWidth: '120px',
  labelPosition: 'right',
  showActions: true,
  submitText: '确定'
})

const emit = defineEmits<{
  (e: 'submit', data: Record<string, any>): void
  (e: 'cancel'): void
}>()

// ==================== Refs ====================

/**
 * 表单渲染器引用
 */
const formRendererRef = ref<InstanceType<typeof FormRenderer>>()

// ==================== State ====================

/**
 * 表单数据
 */
const formData = ref<Record<string, any>>({})

/**
 * 加载状态
 */
const loading = ref(false)

// ==================== 计算属性 ====================

/**
 * 是否为编辑模式
 */
const isEditMode = computed(() => {
  return Object.keys(props.initialData).length > 0
})

// ==================== 方法 ====================

/**
 * 初始化表单数据
 */
function initFormData(): void {
  const data: Record<string, any> = {}

  // 遍历字段，设置默认值
  props.fields.forEach((field) => {
    if (props.initialData[field.fieldName] !== undefined) {
      // 使用初始数据
      data[field.fieldName] = props.initialData[field.fieldName]
    } else {
      // 使用字段默认值或控件默认值
      data[field.fieldName] =
        field.defaultValue ?? getControlDefaultValue(field.controlType)
    }
  })

  formData.value = data
}

/**
 * 处理表单提交
 */
async function handleSubmit(): Promise<void> {
  // 验证表单
  const isValid = await formRendererRef.value?.validate()
  if (!isValid) return

  // 触发提交事件
  emit('submit', { ...formData.value })
}

/**
 * 处理取消操作
 */
function handleCancel(): void {
  emit('cancel')
}

/**
 * 重置表单
 */
function resetForm(): void {
  initFormData()
  formRendererRef.value?.clearValidate()
}

/**
 * 设置加载状态
 *
 * @param state - 加载状态
 */
function setLoading(state: boolean): void {
  loading.value = state
}

/**
 * 获取当前表单数据
 *
 * @returns 表单数据
 */
function getFormData(): Record<string, any> {
  return { ...formData.value }
}

/**
 * 验证表单
 *
 * @returns Promise<boolean>
 */
async function validate(): Promise<boolean> {
  return (await formRendererRef.value?.validate()) || false
}

// ==================== 监听 ====================

/**
 * 监听初始数据变化，重新初始化表单
 */
watch(
  () => props.initialData,
  () => {
    initFormData()
  },
  { immediate: true }
)

/**
 * 监听字段配置变化，重新初始化表单
 */
watch(
  () => props.fields,
  () => {
    initFormData()
  }
)

// ==================== 暴露方法 ====================

defineExpose({
  resetForm,
  setLoading,
  getFormData,
  validate
})
</script>

<style scoped lang="scss">
.dynamic-form {
  width: 100%;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 20px;
  border-top: 1px solid #ebeef5;
  margin-top: 20px;
}
</style>
