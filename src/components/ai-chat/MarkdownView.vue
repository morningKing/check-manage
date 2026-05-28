<script setup lang="ts">
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'
import './md-editor-setup' // register bundled mermaid/echarts (side effect)

defineProps<{ text: string }>()
</script>

<template>
  <!-- wrapper so :deep can reach the MdPreview root (.md-editor); codeFoldable
       =false keeps long code blocks fully visible in the artifact preview. -->
  <div class="markdown-view">
    <MdPreview :modelValue="text" :code-foldable="false" />
  </div>
</template>

<style scoped>
/* md-editor paints a white background by default; make it transparent so it
   inherits the bubble/drawer background (no white box inside colored bubbles). */
.markdown-view :deep(.md-editor) {
  background: transparent;
  --md-bk-color: transparent;
}
.markdown-view :deep(.md-editor-preview) {
  font-size: 14px;
}
</style>
