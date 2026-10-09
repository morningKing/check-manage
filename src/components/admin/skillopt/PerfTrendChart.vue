<template>
  <div ref="el" class="perf-trend" data-test="perf-trend" />
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { useEcharts } from './useEcharts'
import { buildTrendOption } from './chartOptions'
import type { PerfTaskEntry } from '@/api/aiSkills'

const props = defineProps<{ tasks: PerfTaskEntry[] }>()
defineEmits<{ (e: 'open', attemptId: string): void }>()
const el = ref<HTMLElement | null>(null)
const { ready, setOption } = useEcharts(el)

watch([ready, () => props.tasks], () => {
  if (ready.value) setOption(buildTrendOption(props.tasks))
}, { immediate: true, deep: false })
</script>
<style scoped>
.perf-trend { height: 280px; }
</style>
