<template>
  <div v-if="depth > 5" class="subtask-bubble subtask-bubble--capped">
    已达展示深度上限
  </div>
  <div v-else class="subtask-bubble" :class="`subtask-bubble--${status}`">
    <div class="subtask-bubble__head" @click="toggle">
      <ElIcon class="subtask-bubble__chev" :class="{ open }"><ArrowRight /></ElIcon>
      <ElIcon class="subtask-bubble__icon"><MagicStick /></ElIcon>
      <span class="subtask-bubble__agent">{{ agent || '子代理' }}</span>
      <span v-if="description" class="subtask-bubble__desc" :title="description">{{ description }}</span>
      <span class="subtask-bubble__status">
        <ElIcon v-if="status === 'completed'" class="ok"><CircleCheck /></ElIcon>
        <ElIcon v-else-if="status === 'failed'" class="err"><CircleClose /></ElIcon>
        <ElIcon v-else class="run spin"><Loading /></ElIcon>
      </span>
    </div>
    <div v-show="open" class="subtask-bubble__body">
      <div v-if="loading" class="subtask-bubble__loading">加载中…</div>
      <template v-else-if="result">
        <el-alert v-if="result.subtask.status === 'failed' && result.subtask.error"
                  type="error" :closable="false" :title="result.subtask.error" />
        <el-alert v-if="result.truncated" type="info" :closable="false" show-icon
                  :title="`仅显示最近 ${result.messages.length} 条，共 ${result.total} 条`" />
        <el-empty v-if="!result.messages.length" description="子代理还没有对话记录" />
        <div v-for="m in result.messages" :key="m.id" class="subtask-bubble__msg">
          <template v-for="(p, i) in m.content" :key="i">
            <MarkdownView v-if="p.type === 'text' && p.text" :text="p.text" />
            <ToolCallBubble
              v-else-if="p.type === 'tool_use'"
              :name="p.name" :title="p.title" :status="p.status"
              :input="p.input" :result="p.result" :duration-ms="p.durationMs"
            />
            <SubtaskBubble
              v-else-if="p.type === 'subtask_use'"
              :subtask-id="p.subtaskId" :session-id="sessionId"
              :agent="p.agent" :description="p.description" :status="p.status"
              :depth="depth + 1" :fetch-fn="fetchFn"
            />
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElIcon, ElAlert, ElEmpty } from 'element-plus'
import { ArrowRight, MagicStick, CircleCheck, CircleClose, Loading } from '@element-plus/icons-vue'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import ToolCallBubble from '@/components/ai-chat/ToolCallBubble.vue'
import type { SubtaskMessagesResult } from '@/api/aiChat'

// 递归组件需要显式声明 name 才能在自己的模板里引用自己。
defineOptions({ name: 'SubtaskBubble' })

const props = defineProps<{
  subtaskId: string
  sessionId: string
  agent: string | null
  description: string | null
  status: 'running' | 'completed' | 'failed'
  depth: number
  fetchFn: (sessionId: string, subtaskId: string) => Promise<SubtaskMessagesResult>
}>()

const open = ref(false)
const loading = ref(false)
const result = ref<SubtaskMessagesResult | null>(null)

async function toggle() {
  open.value = !open.value
  if (open.value && !result.value) {
    loading.value = true
    try {
      result.value = await props.fetchFn(props.sessionId, props.subtaskId)
    } finally {
      loading.value = false
    }
  }
}
</script>

<style scoped lang="scss">
.subtask-bubble {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  margin: 8px 0;
  background: var(--el-fill-color-lighter);
  font-size: 13px;
  overflow: hidden;
}
.subtask-bubble--capped {
  padding: 8px 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.subtask-bubble__head {
  display: flex; align-items: center; gap: 6px; padding: 8px 12px;
  cursor: pointer; user-select: none;
  &:hover { background: var(--el-fill-color-light); }
}
.subtask-bubble__chev { transition: transform 0.15s; color: var(--el-text-color-secondary); &.open { transform: rotate(90deg); } }
.subtask-bubble__icon { color: var(--el-color-primary); flex-shrink: 0; }
.subtask-bubble__agent { font-weight: 600; color: var(--el-text-color-primary); flex-shrink: 0; }
.subtask-bubble__desc {
  color: var(--el-text-color-secondary); font-size: 12px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; min-width: 0; flex: 1;
}
.subtask-bubble__status { margin-left: auto; flex-shrink: 0; .ok { color: var(--el-color-success); } .err { color: var(--el-color-danger); } .run { color: var(--el-color-primary); } }
.subtask-bubble__body { padding: 4px 12px 12px; border-top: 1px solid var(--el-border-color-lighter); }
.subtask-bubble__loading { padding: 12px; color: var(--el-text-color-secondary); font-size: 12px; }
.subtask-bubble__msg { padding: 8px 0; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
