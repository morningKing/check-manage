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
        <span v-for="(f, i) in files" :key="i" class="kmb__file">📎 {{ f.name }}</span>
      </div>
      <div v-if="time" class="kmb__time">{{ time }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import type { KefuMessage } from '@/api/kefuPublic'

const props = defineProps<{ message: KefuMessage; agentName?: string; agentLogo?: string }>()

// Intentional theme-invariant identity palette (per-agent-name color, like Slack/Gmail avatars — stays constant across light/dark).
const AVATAR_COLORS = ['#5b8def', '#e6795e', '#42b883', '#b06ab3', '#e0913a', '#3aa5c2']

function normalize(content: any) {
  return Array.isArray(content) ? content : [{ type: 'text', text: String(content ?? '') }]
}

const isUser = computed(() => props.message.role === 'user')
const text = computed(() =>
  normalize(props.message.content).filter((p: any) => p.type === 'text').map((p: any) => p.text).join(''))
const files = computed(() =>
  normalize(props.message.content).filter((p: any) => p.type === 'file'))
const initial = computed(() => ((props.agentName || '客服').trim().charAt(0) || '客'))
const avatarStyle = computed(() => {
  const name = props.agentName || '客服'
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return { background: AVATAR_COLORS[h % AVATAR_COLORS.length] }
})
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
  background: var(--el-color-primary, #409eff);
  color: var(--el-color-white, #fff);
  border-bottom-right-radius: 4px;
}
.kmb__file {
  display: inline-block; margin: 4px 4px 0 0; padding: 2px 8px;
  border-radius: 10px; font-size: 12px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  border: 1px solid var(--el-color-primary-light-7, #c6e2ff);
}
/* translucent white overlay: reads correctly on the primary-colored bubble in both light and dark themes */
.kmb--user .kmb__file {
  background: rgba(255, 255, 255, 0.22); border-color: rgba(255, 255, 255, 0.35); color: #fff;
}
.kmb__time { margin-top: 4px; font-size: 11px; color: var(--el-text-color-secondary, #909399); }
/* MarkdownView 首末段去掉多余外边距，贴合气泡 */
.kmb--agent .kmb__bubble :deep(p:first-child) { margin-top: 0; }
.kmb--agent .kmb__bubble :deep(p:last-child) { margin-bottom: 0; }
</style>
