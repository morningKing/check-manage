<script setup lang="ts">
import { computed } from 'vue'
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'
import './md-editor-setup' // register bundled mermaid/echarts (side effect)

const props = defineProps<{ text: string }>()

// We render ```svg``` fences as inline <img> ourselves rather than letting
// markdown-it do it, because md-editor's default validateLink only accepts
// data: URLs whose MIME is image/{gif,png,jpeg,webp} — image/svg+xml is
// rejected, so the markdown `![alt](data:image/svg+xml;...)` would render
// as literal text (the base64 looks like mojibake in the bubble). By
// splitting the text and dropping an <img> directly, we sidestep the
// validation entirely. <img>-loaded SVG is XSS-safe (browser disables
// scripts inside it), so no DOMPurify needed.

interface MdSeg { type: 'md'; text: string }
interface SvgSeg { type: 'svg'; src: string }
type Seg = MdSeg | SvgSeg

// Models routinely emit "Add & Norm" or "X & Y" in <text> nodes — a bare `&`
// is invalid XML and the browser refuses to render the whole SVG
// ("xmlParseEntityRef: no name"). Escape any `&` that isn't already part of a
// proper entity reference (&amp;, &#10;, &#x1F;, …) before encoding.
function sanitizeSvgEntities(svg: string): string {
  return svg.replace(/&(?!(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#x[0-9A-Fa-f]+);)/g, '&amp;')
}

function svgToDataUrl(svg: string): string {
  const safe = sanitizeSvgEntities(svg)
  const bytes = new TextEncoder().encode(safe)
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return `data:image/svg+xml;base64,${btoa(bin)}`
}

const segments = computed<Seg[]>(() => {
  const src = props.text
  const fence = /```svg[^\n]*\n([\s\S]*?)```/g
  const out: Seg[] = []
  let last = 0
  let m: RegExpExecArray | null
  fence.lastIndex = 0
  while ((m = fence.exec(src))) {
    if (m.index > last) out.push({ type: 'md', text: src.slice(last, m.index) })
    const body = m[1].replace(/\n+$/, '').trim()
    if (body) out.push({ type: 'svg', src: svgToDataUrl(body) })
    last = m.index + m[0].length
  }
  if (last < src.length) out.push({ type: 'md', text: src.slice(last) })
  if (!out.length) out.push({ type: 'md', text: src })
  return out
})
</script>

<template>
  <!-- wrapper so :deep can reach the MdPreview root (.md-editor); codeFoldable
       =false keeps long code blocks fully visible in the artifact preview. -->
  <div class="markdown-view">
    <template v-for="(seg, i) in segments" :key="i">
      <img
        v-if="seg.type === 'svg'"
        class="markdown-view__svg" :src="seg.src" alt="inline svg"
      />
      <MdPreview v-else :modelValue="seg.text" :code-foldable="false" />
    </template>
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
.markdown-view__svg {
  display: block;
  max-width: 100%;
  max-height: 360px;
  margin: 6px 0;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: #fff;
}
</style>
