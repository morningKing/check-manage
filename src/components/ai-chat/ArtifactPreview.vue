<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElRadioGroup, ElRadioButton, ElSelect, ElOption, ElButton, ElMessage } from 'element-plus'
import { CopyDocument, Download } from '@element-plus/icons-vue'
import MarkdownView from './MarkdownView.vue'
import { isRenderableLang, isMarkdownLang, downloadText } from '@/utils/artifacts'
import { copyText } from '@/utils/clipboard'

export interface ArtifactVersion { lang: string; code: string }

const props = defineProps<{ filename: string; versions: ArtifactVersion[] }>()

// default to the latest version
const index = ref(props.versions.length - 1)
watch(() => props.versions, () => { index.value = props.versions.length - 1 })

const current = computed(() => props.versions[index.value] ?? props.versions[props.versions.length - 1] ?? { lang: '', code: '' })
const renderable = computed(() => isRenderableLang(current.value.lang))
const mode = ref<'render' | 'source'>('source')
watch(current, () => { mode.value = renderable.value ? 'render' : 'source' }, { immediate: true })

const srcdoc = computed(() => {
  if (current.value.lang.toLowerCase() === 'svg') {
    return `<!doctype html><meta charset="utf-8"><body style="margin:0;display:flex;justify-content:center">${current.value.code}</body>`
  }
  return current.value.code
})
const sourceMarkdown = computed(() =>
  isMarkdownLang(current.value.lang) ? current.value.code : '```' + (current.value.lang || '') + '\n' + current.value.code + '\n```',
)

async function copy() {
  if (await copyText(current.value.code)) ElMessage.success('已复制')
  else ElMessage.error('复制失败')
}
function download() { downloadText(props.filename, current.value.code) }
</script>

<template>
  <div class="artifact-preview">
    <div class="artifact-preview__toolbar">
      <ElSelect v-if="versions.length > 1" v-model="index" size="small" style="width: 130px">
        <ElOption
          v-for="(_, i) in versions" :key="i"
          :label="`版本 ${i + 1}${i === versions.length - 1 ? '（最新）' : ''}`" :value="i"
        />
      </ElSelect>
      <ElRadioGroup v-if="renderable" v-model="mode" size="small">
        <ElRadioButton value="render">渲染</ElRadioButton>
        <ElRadioButton value="source">源码</ElRadioButton>
      </ElRadioGroup>
      <span class="artifact-preview__spacer" />
      <ElButton size="small" :icon="CopyDocument" @click="copy">复制</ElButton>
      <ElButton size="small" type="primary" :icon="Download" @click="download">下载</ElButton>
    </div>
    <div class="artifact-preview__body">
      <iframe
        v-if="renderable && mode === 'render'"
        class="artifact-preview__frame"
        sandbox="allow-scripts"
        :srcdoc="srcdoc"
        title="artifact preview"
      />
      <MarkdownView v-else :text="sourceMarkdown" />
    </div>
  </div>
</template>

<style scoped lang="scss">
.artifact-preview { display: flex; flex-direction: column; height: 100%; }
.artifact-preview__toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-shrink: 0; }
.artifact-preview__spacer { flex: 1; }
.artifact-preview__body { flex: 1; min-height: 0; overflow: auto; }
.artifact-preview__frame { width: 100%; height: 100%; min-height: 60vh; border: 1px solid var(--el-border-color-light); border-radius: 8px; background: #fff; }
</style>
