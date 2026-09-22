<!--
 * 图片区块 Widget
 *
 * 展示一张图片:支持 data-files 上传地址(/api/data-files/<id>/download,
 * 渲染时自动附 access_token)或任意 URL;可选点击跳转与填充模式。
 -->
<template>
  <el-card class="image-widget" shadow="never">
    <div class="image-widget__head">
      <span class="image-widget__title">{{ title || '图片' }}</span>
    </div>
    <a v-if="content.imageUrl && content.link" :href="content.link" target="_blank" rel="noopener">
      <img class="image-widget__img" :src="resolvedUrl" :alt="content.alt || title || '图片'"
           :style="{ objectFit: fit }" loading="lazy" />
    </a>
    <img v-else-if="content.imageUrl" class="image-widget__img" :src="resolvedUrl"
         :alt="content.alt || title || '图片'" :style="{ objectFit: fit }" loading="lazy" />
    <div v-else class="image-widget__empty">
      <el-icon><Picture /></el-icon>
      <span>未配置图片</span>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Picture } from '@element-plus/icons-vue'
import { authedDataFileUrl } from '@/api/dataFiles'
import type { WidgetContentMap } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['image']
  title?: string
  widgetId?: string
}>()

// data-files 地址自动附 access_token(幂等);外链原样
const resolvedUrl = computed(() => authedDataFileUrl(props.content.imageUrl || ''))

const fit = computed(() => props.content.fit || 'contain')
</script>

<style scoped lang="scss">
.image-widget__head { margin-bottom: 8px; }
.image-widget__title { font-weight: 600; font-size: 14px; }
.image-widget__img {
  display: block;
  width: 100%;
  max-height: 420px;
  border-radius: 6px;
}
.image-widget__empty {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 32px 0; color: var(--el-text-color-placeholder);
  background: var(--el-fill-color-light); border-radius: 6px;
}
</style>
