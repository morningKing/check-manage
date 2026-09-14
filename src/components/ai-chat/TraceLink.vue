<script setup lang="ts">
import type { AiTraceMetadata } from '@/types/aiChat'

defineProps<{ trace?: AiTraceMetadata | null }>()
</script>

<template>
  <a
    v-if="trace?.traceUrl"
    :href="trace.traceUrl"
    target="_blank"
    rel="noopener noreferrer"
    aria-label="查看 Trace"
  >查看 Trace</a>
</template>
