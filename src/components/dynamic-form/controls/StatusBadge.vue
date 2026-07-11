/**
 * 状态徽标展示控件
 *
 * 只读：图标 + 文字，颜色取自选项配置，animated 为真时图标持续旋转。
 * 不接受用户交互（没有 change 事件），值只能由第三方系统或后端超时任务回写。
 */
<template>
  <span class="status-badge" :style="{ color: currentOption?.color }">
    <el-icon v-if="currentOption?.icon" :class="{ 'status-badge-spin': currentOption.animated }">
      <component :is="iconComp(currentOption.icon)" />
    </el-icon>
    <span class="status-badge-label">{{ currentOption?.label || modelValue || '-' }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import * as ElIcons from '@element-plus/icons-vue'
import type { FieldConfig } from '@/types'

const props = defineProps<{
  modelValue: string
  field: FieldConfig
}>()

const currentOption = computed(() => {
  const options = props.field.statusBadgeConfig?.options || []
  return options.find(o => o.value === props.modelValue)
})

function iconComp(name: string) {
  return (ElIcons as Record<string, unknown>)[name]
}
</script>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.status-badge-spin {
  animation: status-badge-rotate 1.2s linear infinite;
}

@keyframes status-badge-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
