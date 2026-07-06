<!-- src/components/kefu/KefuMessageBubble.vue -->
<template>
  <div class="kmb" :class="isUser ? 'kmb--user' : 'kmb--agent'">
    <div v-if="!isUser" class="kmb__avatar" :style="avatarStyle">
      <img v-if="agentLogo" :src="agentLogo" alt="" />
      <span v-else>{{ initial }}</span>
    </div>
    <div class="kmb__col">
      <div class="kmb__bubble">
        <MarkdownView v-if="!isUser" :text="text" />
        <span v-else-if="text" class="kmb__text">{{ text }}</span>
        <template v-for="(f, i) in files" :key="i">
          <a v-if="isKefuImage(f.name)" :href="kefuFileUrl(sessionId, f.name)" target="_blank" rel="noopener">
            <img class="kmb__img" :src="kefuFileUrl(sessionId, f.name)" :alt="f.name" />
          </a>
          <a v-else class="kmb__file" :href="kefuFileUrl(sessionId, f.name)" target="_blank" rel="noopener" download>📎 {{ f.name }}</a>
        </template>
      </div>
      <div v-if="time" class="kmb__time">{{ time }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import type { KefuMessage } from '@/api/kefuPublic'
import { kefuFileUrl, isKefuImage } from '@/api/kefuPublic'
import { avatarInitial, avatarColor } from './avatar'

const props = withDefaults(defineProps<{ message: KefuMessage; agentName?: string; agentLogo?: string; sessionId?: string }>(), { sessionId: '' })

function normalize(content: any) {
  return Array.isArray(content) ? content : [{ type: 'text', text: String(content ?? '') }]
}

const isUser = computed(() => props.message.role === 'user')
const text = computed(() =>
  normalize(props.message.content).filter((p: any) => p.type === 'text').map((p: any) => p.text).join(''))
const files = computed(() =>
  normalize(props.message.content).filter((p: any) => p.type === 'file'))
const initial = computed(() => avatarInitial(props.agentName))
const avatarStyle = computed(() => ({ background: avatarColor(props.agentName) }))
const time = computed(() => {
  if (!props.message.createdAt) return ''
  const d = new Date(props.message.createdAt)
  if (isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
})
</script>

<style scoped>
.kmb { display: flex; gap: 8px; margin-bottom: 16px; align-items: flex-start; }
.kmb--user { flex-direction: row-reverse; }
.kmb__avatar {
  flex-shrink: 0; width: 36px; height: 36px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: var(--el-color-white, #fff); font-size: 15px; font-weight: 600; overflow: hidden;
}
.kmb__avatar img { width: 100%; height: 100%; object-fit: cover; }
.kmb__col { display: flex; flex-direction: column; max-width: 76%; }
.kmb--user .kmb__col { align-items: flex-end; }
.kmb__bubble {
  padding: 8px 12px; border-radius: 12px; line-height: 1.5;
  word-break: break-word; overflow-wrap: anywhere;
}
.kmb--agent .kmb__bubble {
  /* white card on the gray page (classic messenger look): --el-fill-color-light
     is nearly identical to the page background, so use --el-bg-color + a border
     for clear contrast against the visitor's primary bubble */
  background: var(--el-bg-color, #fff);
  color: var(--el-text-color-primary, #303133);
  border: 1px solid var(--el-border-color-light, #e4e7ed);
  border-top-left-radius: 4px;
}
.kmb--user .kmb__bubble {
  background: var(--kefu-accent, #4f6ef2);
  color: var(--el-color-white, #fff);
  border-bottom-right-radius: 4px;
}
.kmb__file {
  display: inline-block; margin: 4px 4px 0 0; padding: 2px 8px;
  border-radius: 10px; font-size: 12px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  border: 1px solid var(--el-color-primary-light-7, #c6e2ff);
  text-decoration: none; cursor: pointer;
}
.kmb__img { max-width: 220px; max-height: 220px; border-radius: 8px; display: block; margin-top: 4px; cursor: zoom-in; }
/* translucent white overlay: reads correctly on the primary-colored bubble in both light and dark themes */
.kmb--user .kmb__file {
  background: rgba(255, 255, 255, 0.22); border-color: rgba(255, 255, 255, 0.35); color: #fff;
}
.kmb__time { margin-top: 4px; font-size: 11px; color: var(--el-text-color-secondary, #909399); }
/* MarkdownView 首末段去掉多余外边距，贴合气泡 */
.kmb--agent .kmb__bubble :deep(p:first-child) { margin-top: 0; }
.kmb--agent .kmb__bubble :deep(p:last-child) { margin-bottom: 0; }
</style>
