<template><div class="perf-trend-placeholder" /></template>
<script setup lang="ts">
import type { PerfTaskEntry } from '@/api/aiSkills'
defineProps<{ tasks: PerfTaskEntry[] }>()
defineEmits<{ (e: 'open', attemptId: string): void }>()
</script>
