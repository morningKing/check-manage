<template>
  <span class="skillfit">
    <span class="skillfit-dots" :title="dotsTitle">
      <span v-for="(s, i) in perStep" :key="i" class="skillfit-dot"
            :class="dotClass(s.status)" />
    </span>
    <span class="skillfit-score">{{ score }}</span>
    <span class="skillfit-status">
      <ElTag size="small" :type="tagType" effect="light">{{ statusLabel }}</ElTag>
    </span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElTag } from 'element-plus'

const props = defineProps<{
  /** 拟合步骤总数（不含 skipped） */
  stepsTotal: number
  /** 每步结果（渲染 N 个圆点） */
  perStep: Array<{ status: string }>
  /** 拟合得分 0-100 */
  score: number
  /** 拟合状态：fit/partial/diverged/no_trace/parse_error */
  status: string
}>()

const STATUS_META: Record<string, {
  label: string
  type: 'success' | 'warning' | 'danger' | 'info'
}> = {
  fit: { label: '拟合', type: 'success' },
  partial: { label: '部分拟合', type: 'warning' },
  diverged: { label: '偏离', type: 'danger' },
  no_trace: { label: '无轨迹', type: 'info' },
  parse_error: { label: '解析失败', type: 'info' },
}

const statusLabel = computed(() => STATUS_META[props.status]?.label ?? props.status)
const tagType = computed(() => STATUS_META[props.status]?.type ?? 'info')
const dotsTitle = computed(() => `共 ${props.stepsTotal} 步 · 得分 ${props.score}`)

function dotClass(status: string) {
  if (status === 'hit') return 'is-hit'
  if (status === 'miss') return 'is-miss'
  return 'is-skipped'           // skipped 与未知状态均按灰点中性呈现
}
</script>

<style scoped>
.skillfit { display: inline-flex; align-items: center; gap: 8px; }
.skillfit-dots { display: inline-flex; align-items: center; gap: 3px; }
.skillfit-dot {
  width: 9px; height: 9px; border-radius: 50%; display: inline-block;
}
.skillfit-dot.is-hit { background: var(--el-color-success); }
.skillfit-dot.is-miss { background: var(--el-color-danger); }
.skillfit-dot.is-skipped { background: var(--el-border-color-darker); opacity: .55; }
.skillfit-score { font-weight: 600; font-size: 12.5px; font-variant-numeric: tabular-nums; }
</style>
