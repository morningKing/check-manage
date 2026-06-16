<!--
 * Markdown 只读渲染（统一组件）
 *
 * 首页区块（custom-markdown / system-info）与数据页「查看」弹窗的 Markdown 字段
 * 共用同一套渲染机制：md-editor-v3 的 MdPreview + 透明背景 / 去除多余内边距。
 * 支持完整 GFM：标题、表格、围栏代码块（含语法高亮）、引用、任务列表、分割线、
 * 删除线、嵌套列表等。
 -->
<template>
  <MdPreview class="md-preview" :model-value="text || ''" />
</template>

<script setup lang="ts">
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'

defineProps<{ text?: string | null }>()
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
</style>
