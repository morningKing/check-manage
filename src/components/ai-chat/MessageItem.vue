<script setup lang="ts">
import { computed } from 'vue'
import MarkdownView from './MarkdownView.vue'
import type { AiMessage, AiContentPart } from '@/api/aiChat'

const props = defineProps<{ message: AiMessage }>()

const textParts = computed(() =>
  props.message.content.filter((p): p is Extract<AiContentPart, { type: 'text' }> => p.type === 'text'),
)

const toolParts = computed(() =>
  props.message.content.filter((p): p is Extract<AiContentPart, { type: 'tool_use' }> => p.type === 'tool_use'),
)
</script>

<template>
  <div class="ai-message" :class="['ai-message--' + message.role]">
    <div v-for="(p, i) in textParts" :key="'t' + i" class="ai-message__text">
      <MarkdownView :text="p.text" />
    </div>
    <!-- M1: tool_use parts render as plain JSON; M2 introduces ToolCallBubble -->
    <pre v-for="(p, i) in toolParts" :key="'u' + i" class="ai-message__tool">
{{ '调用工具 ' + p.name + ': ' + JSON.stringify(p.input, null, 2) }}
{{ p.result !== undefined ? '结果: ' + JSON.stringify(p.result, null, 2) : '' }}
    </pre>
  </div>
</template>

<style scoped lang="scss">
.ai-message {
  padding: 8px 12px;
  margin: 6px 0;
  border-radius: 6px;
  &--user      { background: var(--el-color-primary-light-9); }
  &--assistant { background: var(--el-bg-color-page); }
  &--tool      { background: var(--el-color-info-light-9); }
}
.ai-message__tool {
  font-family: monospace;
  font-size: 12px;
  background: var(--el-color-info-light-9);
  padding: 6px;
  border-radius: 4px;
  white-space: pre-wrap;
}
</style>
