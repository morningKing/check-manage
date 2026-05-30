<script setup lang="ts">
import { computed } from 'vue'
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'
import './md-editor-setup' // register bundled mermaid/echarts (side effect)

const props = defineProps<{ text: string }>()

// Convert ```svg ... ``` fences into a markdown image whose source is a data:
// URL of the raw SVG. md-editor renders it as a normal <img> — inline, no
// "click to preview" bubble. <img>-loaded SVG cannot execute scripts (the
// browser disables them), so this is XSS-safe without needing DOMPurify.
function inlineSvgFences(src: string): string {
  return src.replace(/```svg\s*\n([\s\S]*?)\n```/g, (_, body) => {
    const url = `data:image/svg+xml;utf8,${encodeURIComponent(body.trim())}`
    return `![inline svg](${url})`
  })
}

const rendered = computed(() => inlineSvgFences(props.text))
</script>

<template>
  <!-- wrapper so :deep can reach the MdPreview root (.md-editor); codeFoldable
       =false keeps long code blocks fully visible in the artifact preview. -->
  <div class="markdown-view">
    <MdPreview :modelValue="rendered" :code-foldable="false" />
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
/* echarts default container is a small 4:3 box; give it a proper chat-width
   landscape size (echarts reads this at init, so it renders full size). */
.markdown-view :deep(.md-editor-echarts) {
  width: 100% !important;
  height: 360px !important;
  aspect-ratio: auto !important;
}
/* Inlined ```svg``` blocks become <img>; constrain so a huge graphic doesn't
   overrun the bubble. Border matches other inline media (ChatFile). */
.markdown-view :deep(img[alt="inline svg"]) {
  display: block;
  max-width: 100%;
  max-height: 360px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}
</style>
