<!--
 * 系统信息 Widget
 *
 * 用 md-editor-v3 的 MdPreview 渲染，与「首页配置」编辑器（MdEditor 预览）完全一致，
 * 支持完整 GFM（标题/表格/围栏代码块/引用/图片/分割线/列表等）。
 * 此前用手写正则只支持 **加粗** + 列表，其余标签按原文显示。
 -->
<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>{{ title || '系统说明' }}</span>
      </div>
    </template>
    <MdPreview class="system-info" :model-value="content?.markdown || ''" />
  </el-card>
</template>

<script setup lang="ts">
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'
import type { WidgetContentMap } from '@/types'

defineProps<{
  content: WidgetContentMap['system-info']
  title?: string
}>()
</script>

<style scoped lang="scss">
.card-header {
  display: flex;
  align-items: center;
  font-weight: 600;
}

.system-info {
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
