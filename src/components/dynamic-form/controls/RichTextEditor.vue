/**
 * 富文本编辑器控件
 *
 * 使用 @vueup/vue-quill 实现 WYSIWYG 编辑
 * 支持常用格式化工具栏，输出 HTML 格式
 */
<template>
  <div class="richtext-editor" :class="{ 'is-disabled': field.disabled }">
    <QuillEditor
      ref="quillRef"
      v-model:content="content"
      content-type="html"
      :placeholder="field.placeholder || '请输入内容...'"
      :disabled="field.disabled"
      :toolbar="toolbarOptions"
      theme="snow"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { QuillEditor } from '@vueup/vue-quill'
import '@vueup/vue-quill/dist/vue-quill.snow.css'
import type { FieldConfig } from '@/types'

interface Props {
  field: FieldConfig
  modelValue: string | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const quillRef = ref()

// 工具栏配置
const toolbarOptions = [
  ['bold', 'italic', 'underline', 'strike'],        // 加粗、斜体、下划线、删除线
  ['blockquote', 'code-block'],                      // 引用、代码块
  [{ header: 1 }, { header: 2 }],                    // 标题
  [{ list: 'ordered' }, { list: 'bullet' }],        // 有序/无序列表
  [{ indent: '-1' }, { indent: '+1' }],             // 缩进
  [{ size: ['small', false, 'large', 'huge'] }],    // 字体大小
  [{ header: [1, 2, 3, 4, 5, 6, false] }],          // 标题级别
  [{ color: [] }, { background: [] }],              // 字体颜色、背景色
  [{ align: [] }],                                   // 对齐方式
  ['clean'],                                         // 清除格式
  ['link']                                           // 链接
]

// 内容双向绑定
const content = computed({
  get: () => props.modelValue || '',
  set: (value) => emit('update:modelValue', value || '')
})
</script>

<style scoped>
.richtext-editor {
  width: 100%;
}

.richtext-editor :deep(.ql-toolbar) {
  border-top-left-radius: 4px;
  border-top-right-radius: 4px;
  border-color: var(--el-border-color);
  background: var(--el-fill-color-blank);
}

.richtext-editor :deep(.ql-container) {
  border-bottom-left-radius: 4px;
  border-bottom-right-radius: 4px;
  border-color: var(--el-border-color);
  min-height: 200px;
  max-height: 400px;
  overflow-y: auto;
  font-size: 14px;
}

.richtext-editor :deep(.ql-editor) {
  min-height: 180px;
  padding: 12px 15px;
}

.richtext-editor :deep(.ql-editor.ql-blank::before) {
  font-style: normal;
  color: var(--el-text-color-placeholder);
}

.richtext-editor.is-disabled :deep(.ql-toolbar) {
  pointer-events: none;
  opacity: 0.6;
}

.richtext-editor.is-disabled :deep(.ql-container) {
  background: var(--el-disabled-bg-color);
}
</style>