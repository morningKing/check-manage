<template>
  <div class="batch-conversation">
    <el-alert v-if="truncated" type="info" :closable="false" show-icon
              :title="`仅显示最近 ${messages.length} 条，共 ${total} 条`" />
    <el-empty v-if="!loading && !messages.length" description="该子任务还没有对话记录" />
    <div v-for="m in messages" :key="m.id" class="msg" :class="`msg--${m.role}`">
      <div class="msg__role">{{ m.role === 'user' ? '提问' : '助手' }}</div>
      <div class="msg__body">
        <template v-for="(p, i) in mergeReasoningParts(m.content || [])" :key="i">
          <MarkdownView v-if="p.type === 'text' && p.text" :text="p.text" />
          <Thinking
            v-else-if="p.type === 'reasoning' && p.text"
            class="msg__thinking"
            :content="p.text" status="end"
            :model-value="false"
          />
          <el-collapse v-else-if="p.type === 'tool_use'" class="msg__tool">
            <el-collapse-item :title="`工具调用：${p.name || '未知'}`">
              <pre>{{ JSON.stringify(p, null, 2) }}</pre>
            </el-collapse-item>
          </el-collapse>
          <SubtaskBubble
            v-else-if="p.type === 'subtask_use'"
            :subtask-id="p.subtaskId" :session-id="sessionId"
            :agent="p.agent" :description="p.description" :status="p.status"
            :depth="1" :fetch-fn="fetchSubtaskFn"
          />
          <!-- 回合级失败（error part，由 chat_persist 持久化 / 批任务的
               _persist_conversation 透传）——只读视图里也要能看出这轮挂了 -->
          <div v-else-if="p.type === 'error'" class="msg__turn-error">{{ p.text }}</div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Thinking } from 'vue-element-plus-x'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import SubtaskBubble from '@/components/ai-chat/SubtaskBubble.vue'
import { mergeReasoningParts } from '@/utils/artifacts'
import type { AdminMessage } from '@/api/aiBatchAdmin'

// 只读：刻意没有输入框。管理员在别人的会话里发消息语义上说不通（以谁的身份发？）。
defineProps<{
  messages: AdminMessage[]
  truncated: boolean
  total: number
  loading: boolean
  sessionId: string
  fetchSubtaskFn: (sessionId: string, subtaskId: string) => Promise<import('@/api/aiChat').SubtaskMessagesResult>
}>()
</script>

<style scoped lang="scss">
.batch-conversation { max-height: 60vh; overflow-y: auto; }
.msg { padding: 12px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.msg__role { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 6px; }
.msg__tool pre { font-size: 12px; overflow-x: auto; }
.msg__thinking :deep(.elx-thinking__content pre) {
  max-height: 280px;
  overflow-y: auto;
}
.msg__turn-error {
  margin: 8px 0;
  padding: 8px 12px;
  border: 1px solid var(--el-color-danger-light-5);
  border-radius: 8px;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}
</style>
