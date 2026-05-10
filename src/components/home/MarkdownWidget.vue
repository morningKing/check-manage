/**
 * Markdown Widget
 *
 * 支持 Markdown 格式渲染：
 * - Headers: # ## ###
 * - **bold**, *italic*
 * - `code`
 * - Links: [text](url)
 * - Lists (有序/无序)
 */
<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>{{ title || 'Markdown 内容' }}</span>
      </div>
    </template>
    <div class="markdown-content" v-html="renderedMarkdown"></div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { WidgetContentMap } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['custom-markdown']
  title?: string
}>()

/**
 * Markdown 渲染
 * 支持：Headers, bold, italic, code, links, lists
 */
const renderedMarkdown = computed(() => {
  let text = props.content.markdown

  // 处理 Headers: # ## ###
  text = text.replace(/^### (.+)$/gm, '<h3>$1</h3>')
  text = text.replace(/^## (.+)$/gm, '<h2>$1</h2>')
  text = text.replace(/^# (.+)$/gm, '<h1>$1</h1>')

  // 处理 **bold**
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')

  // 处理 *italic*
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>')

  // 处理 `code`
  text = text.replace(/`(.+?)`/g, '<code>$1</code>')

  // 处理 Links: [text](url)
  text = text.replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')

  // 处理有序列表和无序列表
  const lines = text.split('\n')
  const result: string[] = []
  let inUnorderedList = false
  let inOrderedList = false

  for (const line of lines) {
    const trimmed = line.trim()

    // 无序列表: - 或 * 开头
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      if (inOrderedList) {
        result.push('</ol>')
        inOrderedList = false
      }
      if (!inUnorderedList) {
        result.push('<ul>')
        inUnorderedList = true
      }
      // 递归处理列表项内的 inline markdown
      const listItemContent = processInlineMarkdown(trimmed.slice(2))
      result.push(`<li>${listItemContent}</li>`)
    }
    // 有序列表: 数字. 开头
    else if (/^\d+\.\s/.test(trimmed)) {
      if (inUnorderedList) {
        result.push('</ul>')
        inUnorderedList = false
      }
      if (!inOrderedList) {
        result.push('<ol>')
        inOrderedList = true
      }
      const listItemContent = processInlineMarkdown(trimmed.replace(/^\d+\.\s/, ''))
      result.push(`<li>${listItemContent}</li>`)
    }
    else {
      // 关闭列表
      if (inUnorderedList) {
        result.push('</ul>')
        inUnorderedList = false
      }
      if (inOrderedList) {
        result.push('</ol>')
        inOrderedList = false
      }

      // 处理 header 行（已转换）或普通段落
      if (trimmed.startsWith('<h1>') || trimmed.startsWith('<h2>') || trimmed.startsWith('<h3>')) {
        result.push(trimmed)
      } else if (trimmed) {
        const paragraphContent = processInlineMarkdown(trimmed)
        result.push(`<p>${paragraphContent}</p>`)
      }
    }
  }

  // 如果最后还在列表中，关闭列表
  if (inUnorderedList) {
    result.push('</ul>')
  }
  if (inOrderedList) {
    result.push('</ol>')
  }

  return result.join('')
})

/**
 * 处理行内 Markdown 格式
 * (bold, italic, code, links)
 */
function processInlineMarkdown(text: string): string {
  let result = text
  result = result.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  result = result.replace(/\*(.+?)\*/g, '<em>$1</em>')
  result = result.replace(/`(.+?)`/g, '<code>$1</code>')
  result = result.replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
  return result
}
</script>

<style scoped lang="scss">
.card-header {
  display: flex;
  align-items: center;
  font-weight: 600;
}

.markdown-content {
  font-size: 14px;
  color: #606266;
  line-height: 1.8;

  h1 {
    font-size: 24px;
    font-weight: 600;
    color: #303133;
    margin: 16px 0 8px 0;
    padding-bottom: 8px;
    border-bottom: 1px solid #ebeef5;
  }

  h2 {
    font-size: 20px;
    font-weight: 600;
    color: #303133;
    margin: 14px 0 6px 0;
  }

  h3 {
    font-size: 16px;
    font-weight: 600;
    color: #303133;
    margin: 12px 0 4px 0;
  }

  p {
    margin: 8px 0;
  }

  ul, ol {
    margin: 8px 0;
    padding-left: 24px;

    li {
      margin: 4px 0;
    }
  }

  strong {
    font-weight: 600;
    color: #303133;
  }

  em {
    font-style: italic;
    color: #606266;
  }

  code {
    background-color: #f5f7fa;
    border: 1px solid #e4e7ed;
    border-radius: 4px;
    padding: 2px 6px;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 13px;
    color: #e6a23c;
  }

  a {
    color: #409eff;
    text-decoration: none;

    &:hover {
      text-decoration: underline;
    }
  }
}
</style>