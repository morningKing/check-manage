<script setup lang="ts">
import { computed } from 'vue'
import { MdPreview } from 'md-editor-v3'
import { ElIcon, ElMessage } from 'element-plus'
import { CopyDocument, Download } from '@element-plus/icons-vue'
import 'md-editor-v3/lib/preview.css'
import './md-editor-setup' // register bundled mermaid/echarts (side effect)
import { copyText } from '@/utils/clipboard'
import { useDarkMode } from '@/composables/useDarkMode'

const props = defineProps<{ text: string }>()

// md-editor 的 theme 决定正文/代码高亮整套配色（.md-editor-dark 类）。
// 不传时恒为 light：深色模式下深灰正文打在深色背景上几乎不可读。
const isDark = useDarkMode()
const mdTheme = computed(() => (isDark.value ? 'dark' : 'light'))

// We render ```svg``` fences as inline <img> ourselves rather than letting
// markdown-it do it, because md-editor's default validateLink only accepts
// data: URLs whose MIME is image/{gif,png,jpeg,webp} — image/svg+xml is
// rejected, so the markdown `![alt](data:image/svg+xml;...)` would render
// as literal text (the base64 looks like mojibake in the bubble). By
// splitting the text and dropping an <img> directly, we sidestep the
// validation entirely. <img>-loaded SVG is XSS-safe (browser disables
// scripts inside it), so no DOMPurify needed.

interface MdSeg { type: 'md'; text: string }
interface SvgSeg { type: 'svg'; src: string; raw: string }
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
    if (body) {
      const safe = sanitizeSvgEntities(body)
      out.push({ type: 'svg', src: svgToDataUrl(body), raw: safe })
    }
    last = m.index + m[0].length
  }
  if (last < src.length) out.push({ type: 'md', text: src.slice(last) })
  if (!out.length) out.push({ type: 'md', text: src })
  return out
})

async function copySvg(raw: string) {
  if (await copyText(raw)) ElMessage.success('已复制 SVG 源码')
  else ElMessage.error('复制失败')
}
</script>

<template>
  <!-- wrapper so :deep can reach the MdPreview root (.md-editor); codeFoldable
       =false keeps long code blocks fully visible in the artifact preview. -->
  <div class="markdown-view">
    <template v-for="(seg, i) in segments" :key="i">
      <div v-if="seg.type === 'svg'" class="markdown-view__svg-wrap">
        <img class="markdown-view__svg" :src="seg.src" alt="inline svg" />
        <div class="markdown-view__svg-actions">
          <button
            class="markdown-view__svg-btn" type="button"
            title="复制 SVG 源码" aria-label="复制 SVG 源码"
            @click="copySvg(seg.raw)"
          >
            <ElIcon><CopyDocument /></ElIcon>
          </button>
          <a
            class="markdown-view__svg-btn"
            title="下载 SVG" aria-label="下载 SVG"
            :href="seg.src" :download="`diagram-${i + 1}.svg`"
          >
            <ElIcon><Download /></ElIcon>
          </a>
        </div>
      </div>
      <MdPreview v-else :modelValue="seg.text" :theme="mdTheme" :code-foldable="false" />
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
/* 深色模式：md-editor dark 自带调色板正文只有 #999，打在深色背景上对比度
   不足（用户反馈"颜色太浅看不清"）。覆盖为 Element Plus 深色主题的主/
   常规文本色，背景保持透明继承气泡底色。 */
html.dark .markdown-view :deep(.md-editor-dark) {
  --md-color: var(--el-text-color-primary);
  --md-hover-color: var(--el-text-color-regular);
  --md-bk-color: transparent;
  --md-bk-color-outstand: var(--el-fill-color);
  --md-border-color: var(--el-border-color);
  --md-bk-hover-color: var(--el-fill-color-light);
}
/* echarts default container is a small 4:3 box; give it a proper chat-width
   landscape size (echarts reads this at init, so it renders full size). */
.markdown-view :deep(.md-editor-echarts) {
  width: 100% !important;
  height: 360px !important;
  aspect-ratio: auto !important;
}
/* Inlined ```svg``` blocks: fill the bubble width with Claude-style copy /
   download buttons floating top-right of the image. */
.markdown-view__svg-wrap {
  position: relative;
  display: block;
  margin: 6px 0;
}
.markdown-view__svg {
  display: block;
  width: 100%;          /* fill the bubble — a vector at the bubble's natural
                           width is what makes inline rendering useful */
  height: auto;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: #fff;
}
.markdown-view__svg-actions {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 4px;
  opacity: 0.85;
  transition: opacity 0.15s ease;
}
.markdown-view__svg-wrap:hover .markdown-view__svg-actions { opacity: 1; }
.markdown-view__svg-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.95);
  color: var(--el-text-color-regular);
  font-size: 14px;
  cursor: pointer;
  text-decoration: none;
}
.markdown-view__svg-btn:hover {
  background: #fff;
  color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}
</style>
