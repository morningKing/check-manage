<template>
  <div ref="el" class="perf-trend" data-test="perf-trend" />
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { useEcharts } from './useEcharts'
import { buildTrendOption, sourceColor, sortTasksByStartedAt } from './chartOptions'
import type { PerfTaskEntry } from '@/api/aiSkills'

const props = defineProps<{ tasks: PerfTaskEntry[] }>()
const emit = defineEmits<{ (e: 'open', attemptId: string): void }>()
const el = ref<HTMLElement | null>(null)
const { ready, setOption, on } = useEcharts(el)

// 点击柱子 → dataIndex 映射回排序后任务 → 打开下钻（与 buildTrendOption
// 同序，见 sortTasksByStartedAt）。init 失败时 useEcharts 静默降级为 no-op。
on('click', (p: any) => {
  const hit = sortTasksByStartedAt(props.tasks)[p?.dataIndex]
  if (hit?.attemptId) emit('open', hit.attemptId)
})

watch([ready, () => props.tasks], () => {
  if (!ready.value) return
  const sorted = sortTasksByStartedAt(props.tasks)
  const opt = buildTrendOption(props.tasks)
  // spec §5.1：柱色区分 source_type
  const bar = (opt.series as any[]).find(s => s.name === '墙钟')
  if (bar) {
    bar.itemStyle = {
      color: (p: any) => sourceColor(sorted[p?.dataIndex]?.sourceType),
    }
  }
  setOption(opt)
}, { immediate: true, deep: false })
</script>
<style scoped>
.perf-trend { height: 280px; }
</style>
