<script setup lang="ts">
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'

defineProps<{ text: string }>()
</script>

<template>
  <MdPreview :modelValue="text" />
</template>

<style scoped>
:deep(.md-editor-preview) {
  font-size: 14px;
}
</style>
