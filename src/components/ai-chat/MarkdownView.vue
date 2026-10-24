<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { MdPreview } from 'md-editor-v3'
import { ElIcon, ElInput, ElMessage } from 'element-plus'
import { CopyDocument, Download, Link } from '@element-plus/icons-vue'
import 'md-editor-v3/lib/preview.css'
import './md-editor-setup' // register bundled mermaid/echarts (side effect)
import { copyText } from '@/utils/clipboard'
import { useDarkMode } from '@/composables/useDarkMode'
import { getPlantumlServer, plantumlSvgUrl, setPlantumlServer } from './plantuml'

const props = defineProps<{ text: string }>()

// md-editor 的 theme 决定正文/代码高亮整套配色（.md-editor-dark 类）。
// 不传时恒为 light：深色模式下深灰正文打在深色背景上几乎不可读。
const isDark = useDarkMode()
const mdTheme = computed(() => (isDark.value ? 'dark' : 'light'))

// We render ```svg``` / ```plantuml``` fences as inline <img> ourselves rather
// than letting markdown-it do it. For svg, md-editor's default validateLink
// only accepts data: URLs whose MIME is image/{gif,png,jpeg,webp} —
// image/svg+xml is rejected, so the markdown `![alt](data:image/svg+xml;...)`
// would render as literal text (the base64 looks like mojibake in the bubble).
// For plantuml there is no offline JS engine at all: the diagram is hex-encoded
// into an <img> URL pointing at a PlantUML server (see ./plantuml). Both cases
// sidestep link validation entirely. <img>-loaded SVG is XSS-safe (browser
// disables scripts inside it), so no DOMPurify needed.

interface MdSeg { type: 'md'; text: string }
interface SvgSeg { type: 'svg'; src: string; raw: string }
interface PumlSeg { type: 'puml'; code: string }
type Seg = MdSeg | SvgSeg | PumlSeg

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
  // \b keeps compound labels (svgbob, plantuml2…) out of the diagram branches —
  // they are not renderable and stay as ordinary code blocks.
  const fence = /```(svg|plantuml|puml)\b[^\n]*\n([\s\S]*?)```/gi
  const out: Seg[] = []
  let last = 0
  let m: RegExpExecArray | null
  fence.lastIndex = 0
  while ((m = fence.exec(src))) {
    if (m.index > last) out.push({ type: 'md', text: src.slice(last, m.index) })
    const body = m[2].replace(/\n+$/, '').trim()
    if (body) {
      if (m[1].toLowerCase() === 'svg') {
        const safe = sanitizeSvgEntities(body)
        out.push({ type: 'svg', src: svgToDataUrl(body), raw: safe })
      } else {
        out.push({ type: 'puml', code: body })
      }
    }
    last = m.index + m[0].length
  }
  if (last < src.length) out.push({ type: 'md', text: src.slice(last) })
  if (!out.length) out.push({ type: 'md', text: src })
  return out
})

// PlantUML 走服务器渲染：img 加载失败（服务不可达 / 语法有误 / URL 超限）时
// 降级为「源码 + 自建服务配置」卡。失败状态按源码内容记忆——流式期间 segments
// 每个分片都会重算，按内容记才能不让已失败的图闪回 img 态。
const pumlErrors = reactive(new Set<string>())
const pumlServerInput = ref('')
// 重试计数：作为 img 的 key 触发重挂载重新发请求（错误响应不带缓存，
// 同 src 的普通重渲染不会自动重发）。
const pumlRetryTick = ref(0)

onMounted(() => { pumlServerInput.value = getPlantumlServer() })

function markPumlError(code: string) { pumlErrors.add(code) }

function retryPuml() {
  setPlantumlServer(pumlServerInput.value)
  pumlErrors.clear()
  pumlRetryTick.value++
}

function pumlUrl(code: string): string {
  return plantumlSvgUrl(code)
}

async function copySvg(raw: string) {
  if (await copyText(raw)) ElMessage.success('已复制 SVG 源码')
  else ElMessage.error('复制失败')
}

async function copyPuml(code: string) {
  if (await copyText(code)) ElMessage.success('已复制 PlantUML 源码')
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
      <div v-else-if="seg.type === 'puml'" class="markdown-view__svg-wrap markdown-view__puml-wrap">
        <template v-if="!pumlErrors.has(seg.code)">
          <img
            :key="`puml-${pumlRetryTick}`"
            class="markdown-view__svg markdown-view__puml-img"
            :src="pumlUrl(seg.code)"
            alt="PlantUML 图"
            loading="lazy"
            @error="markPumlError(seg.code)"
          />
          <div class="markdown-view__svg-actions">
            <button
              class="markdown-view__svg-btn" type="button"
              title="复制 PlantUML 源码" aria-label="复制 PlantUML 源码"
              @click="copyPuml(seg.code)"
            >
              <ElIcon><CopyDocument /></ElIcon>
            </button>
            <a
              class="markdown-view__svg-btn"
              title="在新窗口打开" aria-label="在新窗口打开"
              :href="pumlUrl(seg.code)" target="_blank" rel="noopener"
            >
              <ElIcon><Link /></ElIcon>
            </a>
          </div>
        </template>
        <div v-else class="markdown-view__puml-err">
          <div class="markdown-view__puml-err-tip">
            PlantUML 渲染失败（服务不可达、语法有误或图过大）。可改为自建 PlantUML 服务后重试：
          </div>
          <div class="markdown-view__puml-err-ops">
            <ElInput
              v-model="pumlServerInput"
              size="small"
              placeholder="https://plantuml.example.com/plantuml"
              @keyup.enter="retryPuml"
            />
            <button class="markdown-view__puml-retry" type="button" @click="retryPuml">重试</button>
          </div>
          <pre class="markdown-view__puml-src">{{ seg.code }}</pre>
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
/* PlantUML 渲染失败降级卡：说明 + 自建服务输入 + 源码兜底展示 */
.markdown-view__puml-err {
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-fill-color-light);
  padding: 8px 10px;
}
.markdown-view__puml-err-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
.markdown-view__puml-err-ops {
  display: flex;
  gap: 8px;
  margin: 6px 0;
}
.markdown-view__puml-retry {
  flex: none;
  padding: 0 14px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: #fff;
  color: var(--el-text-color-regular);
  font-size: 12px;
  cursor: pointer;
}
.markdown-view__puml-retry:hover {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}
.markdown-view__puml-src {
  max-height: 240px;
  overflow: auto;
  margin: 0;
  padding: 8px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--el-fill-color);
  border-radius: 4px;
}
</style>
