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
    <div class="fp-body" v-loading="loading">
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
        <img :src="authedUrl" :alt="file?.name || ''" />
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

const ext = computed(() => (props.file?.name || '').split('.').pop()?.toLowerCase() || '')
const authedUrl = computed(() => (props.file?.url ? authedDataFileUrl(props.file.url) : ''))

const kind = computed(() => {
  const e = ext.value
  if (e === 'docx') return 'docx'
  if (['xlsx', 'xls'].includes(e)) return 'excel'
  if (e === 'pptx') return 'pptx'
  if (e === 'pdf') return 'pdf'
  if (['md', 'markdown'].includes(e)) return 'markdown'
  if (['txt', 'log', 'json', 'csv', 'xml', 'yaml', 'yml', 'ini', 'conf'].includes(e)) return 'text'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(e)) return 'image'
  return 'unsupported'
})

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
}
.fp-office {
  width: 100%;
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
