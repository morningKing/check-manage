<template>
  <div ref="el" class="tt-chart" data-test="turn-timeline-chart" />
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { useEcharts } from './useEcharts'
import { buildTurnTimelineOption, type TurnTimeline } from './turnTimeline'

const props = defineProps<{ timeline: TurnTimeline }>()
const el = ref<HTMLElement | null>(null)
const { ready, setOption } = useEcharts(el)

watch([ready, () => props.timeline], () => {
  if (ready.value) setOption(buildTurnTimelineOption(props.timeline))
}, { immediate: true })
</script>
<style scoped>
.tt-chart { height: 130px; margin-bottom: 8px; }
</style>
