<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElRadioGroup, ElRadioButton } from 'element-plus'
import MarkdownView from './MarkdownView.vue'
import { isRenderableLang, isMarkdownLang } from '@/utils/artifacts'

const props = defineProps<{ lang: string; code: string }>()

const renderable = computed(() => isRenderableLang(props.lang))
const mode = ref<'render' | 'source'>(renderable.value ? 'render' : 'source')
watch(() => props.code, () => { mode.value = renderable.value ? 'render' : 'source' })

// SVG is wrapped in a minimal HTML doc so it lays out; HTML is rendered as-is.
const srcdoc = computed(() => {
  if (props.lang.toLowerCase() === 'svg') {
    return `<!doctype html><meta charset="utf-8"><body style="margin:0;display:flex;justify-content:center">${props.code}</body>`
  }
  return props.code
})

const sourceMarkdown = computed(() =>
  isMarkdownLang(props.lang) ? props.code : '```' + (props.lang || '') + '\n' + props.code + '\n```',
)
</script>

<template>
  <div class="artifact-preview">
    <div v-if="renderable" class="artifact-preview__toolbar">
      <ElRadioGroup v-model="mode" size="small">
        <ElRadioButton value="render">渲染</ElRadioButton>
        <ElRadioButton value="source">源码</ElRadioButton>
      </ElRadioGroup>
    </div>
    <div class="artifact-preview__body">
      <!-- Sandboxed: allow-scripts WITHOUT allow-same-origin → null origin,
           cannot touch our app, cookies, or storage. -->
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
.artifact-preview__toolbar { margin-bottom: 10px; flex-shrink: 0; }
.artifact-preview__body { flex: 1; min-height: 0; overflow: auto; }
.artifact-preview__frame { width: 100%; height: 100%; min-height: 60vh; border: 1px solid var(--el-border-color-light); border-radius: 8px; background: #fff; }
</style>
