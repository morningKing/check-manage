/**
 * MetricCard - 大数字指标卡
 *
 * 显示单个聚合值，支持数字格式化和颜色主题
 */
<template>
  <div class="metric-card">
    <div class="metric-value" :style="{ color: color }">
      {{ formattedValue }}
    </div>
    <div v-if="subtitle" class="metric-subtitle">{{ subtitle }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  value: number
  subtitle?: string
  color?: string
  precision?: number
}>(), {
  color: '#409EFF',
  precision: 0,
})

const formattedValue = computed(() => {
  const v = props.value
  if (v >= 1e8) return (v / 1e8).toFixed(1) + ' 亿'
  if (v >= 1e4) return (v / 1e4).toFixed(1) + ' 万'
  return Number.isInteger(v) && props.precision === 0 ? v.toLocaleString() : v.toFixed(props.precision)
})
</script>

<style scoped>
.metric-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 4px;
}
.metric-value {
  font-size: 36px;
  font-weight: 700;
  line-height: 1.1;
}
.metric-subtitle {
  font-size: 13px;
  color: #909399;
}
</style>
