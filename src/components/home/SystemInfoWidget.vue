/**
 * 系统信息 Widget
 *
 * 简单 Markdown 渲染：**bold**、列表
 */
<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>{{ title || '系统说明' }}</span>
      </div>
    </template>
    <div class="system-info" v-html="renderedMarkdown"></div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { WidgetContentMap } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['system-info']
  title?: string
}>()

/**
 * 简单 Markdown 渲染
 * 支持：**bold**、列表
 */
const renderedMarkdown = computed(() => {
  let text = props.content.markdown

  // 处理 **bold**
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')

  // 处理列表项（以 - 或 * 开头的行）
  const lines = text.split('\n')
  const result: string[] = []
  let inList = false

  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      if (!inList) {
        result.push('<ul>')
        inList = true
      }
      result.push(`<li>${trimmed.slice(2)}</li>`)
    } else {
      if (inList) {
        result.push('</ul>')
        inList = false
      }
      if (trimmed) {
        result.push(`<p>${trimmed}</p>`)
      }
    }
  }

  // 如果最后还在列表中，关闭列表
  if (inList) {
    result.push('</ul>')
  }

  return result.join('')
})
</script>

<style scoped lang="scss">
.card-header {
  display: flex;
  align-items: center;
  font-weight: 600;
}

.system-info {
  font-size: 14px;
  color: #606266;
  line-height: 1.8;

  p {
    margin: 8px 0;
  }

  ul {
    margin: 8px 0;
    padding-left: 20px;

    li {
      margin: 4px 0;
    }
  }

  strong {
    font-weight: 600;
    color: #303133;
  }
}
</style>