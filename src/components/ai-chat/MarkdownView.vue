<script setup lang="ts">
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'

defineProps<{ text: string }>()
</script>

<template>
  <!-- codeFoldable=false: don't collapse long code blocks (the artifact preview
       needs the full content visible). -->
  <MdPreview :modelValue="text" :code-foldable="false" />
</template>

<style scoped>
:deep(.md-editor-preview) {
  font-size: 14px;
}
</style>
