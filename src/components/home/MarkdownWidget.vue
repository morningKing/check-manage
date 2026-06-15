<!--
 * Markdown Widget
 *
 * 用 md-editor-v3 的 MdPreview 渲染，与「首页配置」编辑器（MdEditor 预览）完全一致，
 * 支持完整 GFM：标题 h1~h6、表格、围栏代码块、引用、图片、分割线、嵌套列表、删除线、
 * 任务列表等。此前用手写正则只支持极少子集，导致「预览正常、首页很多标签不渲染」。
 -->
<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>{{ title || 'Markdown 内容' }}</span>
      </div>
    </template>
    <MdPreview class="markdown-content" :model-value="content?.markdown || ''" />
  </el-card>
</template>

<script setup lang="ts">
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'
import type { WidgetContentMap } from '@/types'

defineProps<{
  content: WidgetContentMap['custom-markdown']
  title?: string
}>()
</script>

<style scoped lang="scss">
.card-header {
  display: flex;
  align-items: center;
  font-weight: 600;
}

/* 让 md-editor 预览背景透明，融入卡片；字号与原区块一致 */
.markdown-content {
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
