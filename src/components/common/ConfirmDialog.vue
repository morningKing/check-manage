/**
 * 确认对话框组件
 *
 * 职责：
 * - 封装 Element Plus 确认对话框
 * - 提供统一的删除确认等场景
 */
<template>
  <el-dialog
    v-model="visible"
    :title="title"
    :width="width"
    :close-on-click-modal="false"
    @closed="handleClosed"
  >
    <div class="confirm-content">
      <el-icon class="confirm-icon" :class="iconClass">
        <component :is="iconComponent" />
      </el-icon>
      <span class="confirm-message">{{ message }}</span>
    </div>

    <template #footer>
      <el-button @click="handleCancel" :disabled="loading">
        {{ cancelText }}
      </el-button>
      <el-button
        :type="confirmType"
        @click="handleConfirm"
        :loading="loading"
      >
        {{ confirmText }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * ConfirmDialog 组件脚本
 *
 * Props：
 * - modelValue: 对话框可见性
 * - title: 标题
 * - message: 确认消息
 * - type: 类型（info/warning/danger）
 * - confirmText: 确认按钮文本
 * - cancelText: 取消按钮文本
 * - width: 对话框宽度
 *
 * Events：
 * - confirm: 确认
 * - cancel: 取消
 */
import { computed, ref } from 'vue'
import {
  InfoFilled,
  WarningFilled,
  CircleCloseFilled
} from '@element-plus/icons-vue'

// ==================== Props & Emits ====================

interface Props {
  /** 对话框可见性 */
  modelValue: boolean
  /** 标题 */
  title?: string
  /** 确认消息 */
  message: string
  /** 类型 */
  type?: 'info' | 'warning' | 'danger'
  /** 确认按钮文本 */
  confirmText?: string
  /** 取消按钮文本 */
  cancelText?: string
  /** 对话框宽度 */
  width?: string
}

const props = withDefaults(defineProps<Props>(), {
  title: '确认',
  type: 'warning',
  confirmText: '确定',
  cancelText: '取消',
  width: '420px'
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()

// ==================== State ====================

/**
 * 加载状态
 */
const loading = ref(false)

// ==================== 计算属性 ====================

/**
 * 对话框可见性
 */
const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

/**
 * 图标组件
 */
const iconComponent = computed(() => {
  switch (props.type) {
    case 'info':
      return InfoFilled
    case 'danger':
      return CircleCloseFilled
    default:
      return WarningFilled
  }
})

/**
 * 图标样式类
 */
const iconClass = computed(() => {
  return `icon-${props.type}`
})

/**
 * 确认按钮类型
 */
const confirmType = computed(() => {
  return props.type === 'danger' ? 'danger' : 'primary'
})

// ==================== 方法 ====================

/**
 * 处理确认
 */
function handleConfirm(): void {
  emit('confirm')
}

/**
 * 处理取消
 */
function handleCancel(): void {
  visible.value = false
  emit('cancel')
}

/**
 * 对话框关闭后
 */
function handleClosed(): void {
  loading.value = false
}

/**
 * 设置加载状态
 */
function setLoading(state: boolean): void {
  loading.value = state
}

/**
 * 关闭对话框
 */
function close(): void {
  visible.value = false
}

// ==================== 暴露方法 ====================

defineExpose({
  setLoading,
  close
})
</script>

<style scoped lang="scss">
.confirm-content {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px 0;
}

.confirm-icon {
  font-size: 24px;
  flex-shrink: 0;

  &.icon-info {
    color: #909399;
  }

  &.icon-warning {
    color: #e6a23c;
  }

  &.icon-danger {
    color: #f56c6c;
  }
}

.confirm-message {
  font-size: 14px;
  color: #606266;
  line-height: 1.6;
  word-break: break-word;
}
</style>
