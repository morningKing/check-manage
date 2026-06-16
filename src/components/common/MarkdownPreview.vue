<!--
 * Markdown 只读渲染（统一组件）
 *
 * 首页区块（custom-markdown / system-info）与数据页「查看」弹窗的 Markdown 字段
 * 共用同一套渲染机制：md-editor-v3 的 MdPreview + 透明背景 / 去除多余内边距。
 * 支持完整 GFM：标题、表格、围栏代码块（含语法高亮）、引用、任务列表、分割线等。
 *
 * SVG 支持：把内容里的「```svg 代码块」和「裸 <svg>…</svg> 块」抽出来，单独以
 * <img src="data:image/svg+xml;…"> 渲染。<img> 加载的 SVG 浏览器会禁用脚本（XSS 安全），
 * 且不受 md-editor 的 HTML 净化/链接校验限制——所以含 <style>/<defs>/<marker> 的
 * 复杂 SVG（如功能架构图）也能完整渲染。
 -->
<template>
  <div class="md-preview">
    <template v-for="(seg, i) in segments" :key="i">
      <img v-if="seg.type === 'svg'" class="md-preview__svg" :src="seg.src" alt="svg" />
      <MdPreview v-else :model-value="seg.text" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'

const props = defineProps<{ text?: string | null }>()

interface MdSeg { type: 'md'; text: string }
interface SvgSeg { type: 'svg'; src: string }
type Seg = MdSeg | SvgSeg

// 转义不是合法实体引用的裸 `&`（否则 <img> 加载 SVG 会因 XML 解析失败而空白）
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
  const src = props.text || ''
  // 同时匹配 ```svg 围栏块 与 裸 <svg>…</svg> 块
  const re = /```svg[^\n]*\n([\s\S]*?)```|(<svg[\s\S]*?<\/svg>)/gi
  const out: Seg[] = []
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(src))) {
    if (m.index > last) out.push({ type: 'md', text: src.slice(last, m.index) })
    const body = (m[1] || m[2] || '').trim()
    if (body) out.push({ type: 'svg', src: svgToDataUrl(body) })
    last = m.index + m[0].length
  }
  if (last < src.length) out.push({ type: 'md', text: src.slice(last) })
  if (!out.length) out.push({ type: 'md', text: src })
  return out
})
</script>

<style scoped lang="scss">
.md-preview {
  :deep(.md-editor-preview-wrapper) {
    padding: 0;
  }
  &:deep(.md-editor) {
    background: transparent;
    --md-bk-color: transparent;
  }
  :deep(.md-editor-preview) {
    font-size: 14px;
  }
}
.md-preview__svg {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 8px 0;
}
</style>
