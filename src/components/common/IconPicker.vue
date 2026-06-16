<!--
 * 图标选择器：从 Element Plus 全部图标里可搜索地选一个（可清空=不选）。
 * 选项与已选项都直接渲染图标，所见即所得。
 -->
<template>
  <el-select
    v-model="value"
    filterable
    clearable
    placeholder="选择图标（可选）"
    style="width: 100%"
  >
    <template v-if="value && iconComp(value)" #prefix>
      <el-icon><component :is="iconComp(value)" /></el-icon>
    </template>
    <el-option v-for="name in iconNames" :key="name" :label="name" :value="name">
      <span class="icon-opt">
        <el-icon><component :is="iconComp(name)" /></el-icon>
        <span>{{ name }}</span>
      </span>
    </el-option>
  </el-select>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import * as ElIcons from '@element-plus/icons-vue'

const props = defineProps<{ modelValue?: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [v: string] }>()

const value = computed({
  get: () => props.modelValue || '',
  set: (v) => emit('update:modelValue', v),
})

// @element-plus/icons-vue 导出的全部图标组件名
const iconNames = Object.keys(ElIcons).filter((k) => k !== 'default')
function iconComp(name: string) {
  return (ElIcons as Record<string, unknown>)[name]
}
</script>

<style scoped>
.icon-opt {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
