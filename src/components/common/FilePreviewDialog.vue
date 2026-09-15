<!--
 * 文件在线预览弹窗
 *
 * 按扩展名选择渲染器：
 *   Word(docx) / Excel(xlsx,xls) / PPT(pptx) / PDF  → @vue-office/*
 *   Markdown(md)                                    → MarkdownPreview（md-editor-v3）
 *   文本(txt,log,json,csv,xml,yaml…)                → <pre>
 *   图片(png,jpg…)                                   → <img>
 *   其它                                            → 下载兜底
 *
 * 本组件由 DynamicPage 以 defineAsyncComponent 懒加载，所以 @vue-office 等重型库
 * 只在「打开预览」时才加载，不进主包。
 -->
<template>
  <el-dialog
    v-model="visible"
    :title="file?.name || '文件预览'"
    width="84%"
    top="5vh"
    destroy-on-close
    class="file-preview-dialog"
  >
    <div class="fp-body" :class="{ 'fp-body--office': !!officeComponent }" v-loading="loading">
      <!-- Office / PDF -->
      <component
        :is="officeComponent"
        v-if="officeComponent && !errorMsg"
        :key="authedUrl"
        :src="authedUrl"
        class="fp-office"
        @rendered="loading = false"
        @error="onOfficeError"
      />
      <!-- Markdown -->
      <MarkdownPreview v-else-if="kind === 'markdown' && !errorMsg" :text="textContent" />
      <!-- 纯文本 -->
      <pre v-else-if="kind === 'text' && !errorMsg" class="fp-text">{{ textContent }}</pre>
      <!-- 图片 -->
      <div v-else-if="kind === 'image' && !errorMsg" class="fp-image">
        <img :src="imageObjectUrl || authedUrl" :alt="file?.name || ''" @error="onImageError" />
      </div>
      <!-- 兜底 / 错误 -->
      <div v-else class="fp-fallback">
        <el-icon :size="44"><Document /></el-icon>
        <p>{{ errorMsg ? `预览失败：${errorMsg}` : '该格式暂不支持在线预览' }}</p>
        <el-button type="primary" @click="downloadFile">下载文件</el-button>
      </div>
    </div>
    <template #footer>
      <el-button @click="downloadFile">下载</el-button>
      <el-button type="primary" @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Document } from '@element-plus/icons-vue'
// @ts-ignore - @vue-office 无类型声明
import VueOfficeDocx from '@vue-office/docx'
import '@vue-office/docx/lib/index.css'
// @ts-ignore
import VueOfficeExcel from '@vue-office/excel'
import '@vue-office/excel/lib/index.css'
// @ts-ignore
import VueOfficePptx from '@vue-office/pptx'
// @ts-ignore
import VueOfficePdf from '@vue-office/pdf'
import MarkdownPreview from './MarkdownPreview.vue'
import { authedDataFileUrl } from '@/api/dataFiles'
import { previewKind } from '@/utils/filePreview'

interface PreviewFile { name?: string; url?: string; type?: string }

const props = defineProps<{ modelValue: boolean; file: PreviewFile | null }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean] }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const loading = ref(false)
const textContent = ref('')
const errorMsg = ref('')

// authedDataFileUrl 对已带 access_token= 的 url 是幂等的（见 dataFiles.ts）——
// AI 助手产出文件/变更文件预览传入的 url 已经带了自己模块 authParam() 加的
// token，这里不会重复叠加。
const authedUrl = computed(() => (props.file?.url ? authedDataFileUrl(props.file.url) : ''))
const kind = computed(() => previewKind(props.file?.name))

const officeComponent = computed(() => {
  switch (kind.value) {
    case 'docx': return VueOfficeDocx
    case 'excel': return VueOfficeExcel
    case 'pptx': return VueOfficePptx
    case 'pdf': return VueOfficePdf
    default: return null
  }
})

function onOfficeError(e: any) {
  errorMsg.value = String(e?.message || e || '渲染失败')
  loading.value = false
}

// 图片经 Blob objectURL 渲染,而不是直接 <img src=下载链接>:下载接口对文件
// 统一带 Content-Disposition: attachment(供"下载"按钮),而浏览器会拒绝把带
// attachment 头的资源放进 <img> 渲染(onerror),SVG/图片预览于是必然失败。
// fetch + objectURL 不受该头影响,且 blob URL 下 SVG 内嵌脚本不会执行,不引入
// 后端改 inline 的 XSS 面。
const imageObjectUrl = ref('')
let lastImageObjectUrl = ''

function onImageError() {
  if (imageObjectUrl.value) return  // blob 加载失败时避免递归触发兜底 src
  errorMsg.value = '图片加载失败'
  loading.value = false
}

// SVG 必须是严格 XML 才能被 <img> 渲染,但 LLM 生成的 SVG 常带未转义的裸 `&`
// (如 "Add & Layer Norm")——HTML 解析器(会话内制品卡的 iframe)容忍它,严格
// XML 解析(img/blob)直接 broken。这里把不构成实体引用的裸 `&` 转义成 `&amp;`
// (已是 &amp;/&#123;/&#x1F; 形式的不动),与 HTML5 解析器的容错行为一致。
function repairSvgText(text: string): string {
  return text.replace(/&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#[0-9]+|#x[0-9a-fA-F]+);)/g, '&amp;')
}

async function loadImage() {
  loading.value = true
  errorMsg.value = ''
  try {
    const resp = await fetch(authedUrl.value)
    if (!resp.ok) throw new Error('HTTP ' + resp.status)
    let blob = await resp.blob()
    if (kind.value === 'image' && (props.file?.name || '').toLowerCase().endsWith('.svg')) {
      blob = new Blob([repairSvgText(await blob.text())], { type: 'image/svg+xml' })
    }
    if (lastImageObjectUrl) URL.revokeObjectURL(lastImageObjectUrl)
    lastImageObjectUrl = URL.createObjectURL(blob)
    imageObjectUrl.value = lastImageObjectUrl
  } catch (e: any) {
    errorMsg.value = e?.message || '图片加载失败'
  } finally {
    loading.value = false
  }
}

function downloadFile() {
  if (authedUrl.value) window.open(authedUrl.value, '_blank')
}

async function loadText() {
  loading.value = true
  errorMsg.value = ''
  try {
    const resp = await fetch(authedUrl.value)
    if (!resp.ok) throw new Error('HTTP ' + resp.status)
    textContent.value = await resp.text()
  } catch (e: any) {
    errorMsg.value = e?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.modelValue, props.file] as const,
  ([open]) => {
    if (!open || !props.file) return
    errorMsg.value = ''
    textContent.value = ''
    if (kind.value === 'markdown' || kind.value === 'text') {
      loadText()
    } else if (kind.value === 'image') {
      loadImage()
    } else if (officeComponent.value) {
      loading.value = true // office 组件渲染完成会触发 @rendered 置为 false
    } else {
      loading.value = false
    }
  },
  { immediate: true },
)
</script>

<style scoped lang="scss">
.fp-body {
  min-height: 200px;
  max-height: 78vh;
  overflow: auto;

  // @vue-office/excel 内部（x-spreadsheet）在 onMounted 时用 JS 读取容器的
  // clientHeight 来定尺寸，而不是纯 CSS 自适应；父级只有 min-height/max-height
  // （即 auto 高度）时 clientHeight 在挂载那一刻测不出有效值，会退化成写死的
  // 300px 画布——弹窗本身很大，但表格只画了一小块，看起来"高度不对/预览不正常"。
  // 展示 Office 类预览（含 Excel）时给一个确定的 height，让 .fp-office 的
  // height:100% 能一路解析到具体像素值。
  &.fp-body--office {
    height: 78vh;
  }
}
.fp-office {
  width: 100%;
  height: 100%;
}
.fp-text {
  margin: 0;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 4px;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-all;
}
.fp-image {
  text-align: center;
  img { max-width: 100%; height: auto; }
}
.fp-fallback {
  text-align: center;
  padding: 48px 0;
  color: var(--el-text-color-secondary);
  p { margin: 12px 0; }
}
</style>
