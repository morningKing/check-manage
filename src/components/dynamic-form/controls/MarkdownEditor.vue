/**
 * Markdown 编辑器控件
 *
 * 使用 md-editor-v3 的 MdEditor 实现：左侧编写 Markdown、右侧实时渲染预览。
 * 存储的是 Markdown 源文本；只读展示由 DataTable / 详情视图用 MdPreview 渲染。
 */
<template>
  <div class="markdown-editor" :class="{ 'is-disabled': field.disabled }">
    <MdEditor
      v-model="content"
      :placeholder="field.placeholder || '请输入 Markdown 内容...'"
      :disabled="field.disabled"
      :preview="true"
      :toolbars="toolbars"
      :footers="['markdownTotal', '=', 'scrollSwitch']"
      :show-code-row-number="true"
      language="zh-CN"
      style="height: 360px"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { MdEditor, type ToolbarNames } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
import type { FieldConfig } from '@/types'

interface Props {
  field: FieldConfig
  modelValue: string | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

// 精简但够用的工具栏（去掉上传图片/保存等与本场景无关的项）
const toolbars: ToolbarNames[] = [
  'bold', 'underline', 'italic', 'strikeThrough', '-',
  'title', 'sub', 'sup', 'quote', 'unorderedList', 'orderedList', 'task', '-',
  'codeRow', 'code', 'link', 'table', 'mermaid', '-',
  'revoke', 'next', '=', 'preview', 'previewOnly', 'catalog',
]

const content = computed({
  get: () => props.modelValue || '',
  set: (value) => emit('update:modelValue', value || ''),
})
</script>

<style scoped>
.markdown-editor {
  width: 100%;
}
.markdown-editor.is-disabled {
  opacity: 0.7;
  pointer-events: none;
}
</style>
