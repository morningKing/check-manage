<!-- 诊断列表（占位，Task 8 实体化：接收 anchorResolver 做「定位」跳转） -->
<template>
  <div class="diag-list">
    <div v-for="(d, i) in diagnoses" :key="i" class="diag" :class="d.severity">{{ d.text }}</div>
  </div>
</template>
<script setup lang="ts">
import type { Diagnosis } from '@/api/aiSkills'
defineProps<{ diagnoses: Diagnosis[] }>()
</script>
