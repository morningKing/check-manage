<template>
  <div v-if="depth > 5" class="subtask-bubble subtask-bubble--capped">
    已达展示深度上限
  </div>
  <div v-else class="subtask-bubble" :class="`subtask-bubble--${status}`">
    <div class="subtask-bubble__head" @click="toggle">
      <ElIcon class="subtask-bubble__chev" :class="{ open }"><ArrowRight /></ElIcon>
      <ElIcon class="subtask-bubble__icon"><MagicStick /></ElIcon>
      <span class="subtask-bubble__agent">{{ agent || '子代理' }}</span>
      <span
        v-if="shortTaskId" class="subtask-bubble__taskid" :title="`子会话 task_id：${subtaskId}（点击复制）`"
        @click.stop="copyTaskId"
      >{{ shortTaskId }}</span>
      <span v-if="reuseCount > 1" class="subtask-bubble__reuse" title="该子代理会话被多个任务段复用（展开可分段查看）">
        已复用·{{ reuseCount }} 段
      </span>
      <span v-if="description" class="subtask-bubble__desc" :title="description">{{ description }}</span>
      <span class="subtask-bubble__status">
        <ElIcon v-if="status === 'completed'" class="ok"><CircleCheck /></ElIcon>
        <ElIcon v-else-if="status === 'failed'" class="err"><CircleClose /></ElIcon>
        <ElIcon v-else class="run spin"><Loading /></ElIcon>
      </span>
      <button
        v-if="status !== 'running'"
        class="subtask-bubble__compact" type="button"
        title="压缩此子代理的上下文（后续 task_id 续跑基于总结继续）"
        :disabled="compacting" @click.stop="onCompact"
      ><ElIcon><Brush /></ElIcon></button>
    </div>
    <div v-show="open" class="subtask-bubble__body">
      <div v-if="loading" class="subtask-bubble__loading">加载中…</div>
      <template v-else-if="result">
        <el-alert v-if="result.subtask.status === 'failed' && result.subtask.error"
                  type="error" :closable="false" :title="result.subtask.error" />
        <el-alert v-if="result.truncated" type="info" :closable="false" show-icon
                  :title="`仅显示最近 ${result.messages.length} 条，共 ${result.total} 条`" />
        <el-empty v-if="!result.messages.length" description="子代理还没有对话记录" />
        <div
          v-for="m in result.messages" :key="m.id"
          class="subtask-bubble__msg"
          :class="{ 'subtask-bubble__msg--boundary': boundaryOf(m) }"
        >
          <!-- 会话复用：每条 user 消息 = 一次委派任务的起点，渲染任务段边界 -->
          <div v-if="boundaryOf(m)" class="subtask-bubble__segment">
            <span class="subtask-bubble__segment-line" />
            <span class="subtask-bubble__segment-tag">
              任务段 {{ boundaryOf(m)!.ord + 1 }}
              <template v-if="boundaryOf(m)!.turn != null"> · 第 {{ boundaryOf(m)!.turn + 1 }} 轮派发</template>
            </span>
            <span class="subtask-bubble__segment-label" :title="boundaryOf(m)!.label">{{ boundaryOf(m)!.label }}</span>
            <span class="subtask-bubble__segment-line" />
          </div>
          <div v-if="m.role === 'user'" class="subtask-bubble__role">{{ boundaryOf(m) ? '本段任务输入' : '委托输入' }}</div>
          <template v-for="(p, i) in mergeReasoningParts(m.content)" :key="i">
            <MarkdownView v-if="p.type === 'text' && p.text" :text="p.text" />
            <Thinking
              v-else-if="p.type === 'reasoning' && p.text"
              class="subtask-bubble__thinking"
              :content="p.text" status="end"
              :model-value="false"
            />
            <ToolCallBubble
              v-else-if="p.type === 'tool_use'"
              :name="p.name" :title="p.title" :status="p.status"
              :input="p.input" :result="p.result" :duration-ms="p.durationMs"
            />
            <SubtaskBubble
              v-else-if="p.type === 'subtask_use'"
              :subtask-id="p.subtaskId" :session-id="sessionId"
              :agent="p.agent" :description="p.description" :status="p.status"
              :depth="depth + 1" :segment-count="p.segmentCount" :fetch-fn="fetchFn"
            />
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import { ElIcon, ElAlert, ElEmpty, ElMessage, ElMessageBox } from 'element-plus'
import { ArrowRight, MagicStick, CircleCheck, CircleClose, Loading, Brush } from '@element-plus/icons-vue'
import { Thinking } from 'vue-element-plus-x'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import ToolCallBubble from '@/components/ai-chat/ToolCallBubble.vue'
import { mergeReasoningParts } from '@/utils/artifacts'
import { compactSubtask, type SubtaskMessagesResult, type SubtaskSegment } from '@/api/aiChat'

// 递归组件需要显式声明 name 才能在自己的模板里引用自己。
defineOptions({ name: 'SubtaskBubble' })

const props = defineProps<{
  subtaskId: string
  sessionId: string
  agent: string | null
  description: string | null
  status: 'running' | 'completed' | 'failed'
  depth: number
  /** 持久化内容里的任务段数（来自 subtask_use part；未拉取时也能显示徽标） */
  segmentCount?: number
  fetchFn: (sessionId: string, subtaskId: string) => Promise<SubtaskMessagesResult>
}>()

const open = ref(false)
const loading = ref(false)
const result = ref<SubtaskMessagesResult | null>(null)

// ── 会话复用标识（需求：subagent 必须有 session_id/task_id 标识 + 任务段边界）──
const shortTaskId = computed(() => {
  const id = props.subtaskId || ''
  return id.length > 14 ? `${id.slice(0, 12)}…` : id
})
const segments = computed<SubtaskSegment[]>(
  () => result.value?.subtask?.segments || [])
const reuseCount = computed(() => {
  const fromFetch = segments.value.length
  const fromPart = props.segmentCount || 0
  return Math.max(fromFetch, fromPart, 0)
})
// 消息 → 任务段：user 消息按 id 匹配段首；无 segments 数据时（旧数据/单段）
// 保持原「委托输入」形态不加边界。
function boundaryOf(m: { id: string; role: string }): SubtaskSegment | null {
  if (!segments.value.length) return null
  if (m.role === 'user') {
    return segments.value.find((s) => s.firstMsgId === m.id) || null
  }
  return null
}
async function copyTaskId() {
  try {
    await navigator.clipboard.writeText(props.subtaskId)
    ElMessage.success(`子会话 task_id 已复制：${props.subtaskId}`)
  } catch {
    ElMessage({ message: `task_id：${props.subtaskId}`, type: 'info', duration: 10000, showClose: true })
  }
}

// 子代理运行中，展开的气泡要能看到轨迹实时推进：服务端后台监听器会把
// 子代理消息增量写进 ai_chat_subtask_messages（REST 端点现查现新），
// 这里在「展开 + running」期间轮询刷新；到终态拉一次完整数据后停止。
// 修复"必须跳走再跳回来才能看到子代理新消息"的问题。
const POLL_MS = 2500
let pollTimer: ReturnType<typeof setTimeout> | null = null

// 终态但轨迹还没有落库的兜底：子代理的消息由服务端在**父回合 idle 时**
// 统一落库，而「已完成」状态经 SSE 先到——用户此刻展开会拿到空消息列表
// 并永远停在「还没有对话记录」。对空结果做有限次静默重拉，直到轨迹可见。
const EMPTY_RETRY_MS = 5000
const EMPTY_RETRY_MAX = 24 // 约 2 分钟后放弃，等待手动收起/展开触发重取
let emptyRetryTimer: ReturnType<typeof setTimeout> | null = null
let emptyRetries = 0

function stopEmptyRetry() {
  if (emptyRetryTimer) { clearTimeout(emptyRetryTimer); emptyRetryTimer = null }
}

function scheduleEmptyRetry() {
  stopEmptyRetry()
  if (!open.value) return
  // 取到消息即收敛；取数失败（result 仍为 null）与空结果同样需要重试——
  // 终态下没有轮询兜底，一次瞬时失败就会让气泡永远空着。
  if (result.value && result.value.messages.length > 0) { emptyRetries = 0; return }
  if (props.status === 'running') return // running 分支已有轮询覆盖
  if (emptyRetries >= EMPTY_RETRY_MAX) return
  emptyRetries += 1
  emptyRetryTimer = setTimeout(() => { void refresh() }, EMPTY_RETRY_MS)
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
}

async function refresh(withLoading = false) {
  if (withLoading) loading.value = true
  try {
    result.value = await props.fetchFn(props.sessionId, props.subtaskId)
    scheduleEmptyRetry()
  } catch { /* 保留上次快照，下一轮轮询重试 */ }
  finally {
    if (withLoading) loading.value = false
  }
}

function schedulePoll() {
  stopPolling()
  pollTimer = setTimeout(async () => {
    if (!open.value || props.status !== 'running') return
    await refresh()
    // REST 返回的子任务状态优先于父级 part 的快照：已终态就收尾
    if (result.value && result.value.subtask.status === 'running') schedulePoll()
    else stopPolling()
  }, POLL_MS)
}

watch(
  () => [open.value, props.status] as const,
  ([o, st]) => {
    if (!o) { stopPolling(); stopEmptyRetry(); emptyRetries = 0; return }
    if (st === 'running') {
      stopEmptyRetry()
      void refresh(!result.value)   // 首次展开带 loading，其后静默刷新
      schedulePoll()
    } else {
      // 刚到终态（running → completed/failed）：拉最终完整轨迹后停
      void refresh(true)
      stopPolling()
    }
  },
)
onUnmounted(() => { stopPolling(); stopEmptyRetry() })

async function toggle() {
  open.value = !open.value
}

// 压缩此子代理会话：清掉已缓存的轨迹，压缩完成后重拉即可看到总结
// （总结由服务端持久化监听器更新进 ai_chat_subtask_messages）。
const compacting = ref(false)
async function onCompact() {
  if (compacting.value) return
  try {
    await ElMessageBox.confirm(
      '压缩会把该子代理的对话历史总结成精简上下文，后续用 task_id 续跑时基于总结继续（原始轨迹仍可回看）。继续？',
      '压缩子代理上下文',
      { confirmButtonText: '压缩', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  compacting.value = true
  try {
    const res = await compactSubtask(props.sessionId, props.subtaskId)
    ElMessage.success(res.message || '已开始压缩子代理上下文')
    if (open.value) {
      // 压缩回合约需 10-60s；延迟重拉一次让总结在展开态直接可见
      setTimeout(async () => {
        loading.value = true
        try { result.value = await props.fetchFn(props.sessionId, props.subtaskId) }
        finally { loading.value = false }
      }, 20000)
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || e?.message || '压缩失败')
  } finally {
    compacting.value = false
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
.subtask-bubble__compact {
  margin-left: auto;
  display: inline-flex; align-items: center;
  border: none; background: transparent; cursor: pointer;
  color: var(--el-text-color-secondary);
  padding: 2px; border-radius: 4px;
  &:hover { color: var(--el-color-primary); background: var(--el-fill-color); }
  &:disabled { opacity: 0.5; cursor: default; }
}
.subtask-bubble__chev { transition: transform 0.15s; color: var(--el-text-color-secondary); &.open { transform: rotate(90deg); } }
.subtask-bubble__icon { color: var(--el-color-primary); flex-shrink: 0; }
.subtask-bubble__agent { font-weight: 600; color: var(--el-text-color-primary); flex-shrink: 0; }
.subtask-bubble__taskid {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 11px; color: var(--el-text-color-secondary);
  background: var(--el-fill-color); border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px; padding: 0 4px; cursor: pointer; flex-shrink: 0;
  &:hover { color: var(--el-color-primary); border-color: var(--el-color-primary-light-5); }
}
.subtask-bubble__reuse {
  font-size: 11px; color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: 4px; padding: 0 4px; flex-shrink: 0; white-space: nowrap;
}
.subtask-bubble__segment {
  display: flex; align-items: center; gap: 6px;
  margin: 10px 0 2px; color: var(--el-text-color-secondary); font-size: 11px;
}
.subtask-bubble__segment-line { flex: 1; height: 1px; background: var(--el-border-color-lighter); }
.subtask-bubble__segment-tag {
  font-weight: 600; color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
  border-radius: 4px; padding: 0 6px; white-space: nowrap;
}
.subtask-bubble__segment-label {
  max-width: 46%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.subtask-bubble__msg--boundary { border-top: 1px dashed var(--el-border-color-lighter); margin-top: 4px; }
.subtask-bubble__desc {
  color: var(--el-text-color-secondary); font-size: 12px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; min-width: 0; flex: 1;
}
.subtask-bubble__status { margin-left: auto; flex-shrink: 0; .ok { color: var(--el-color-success); } .err { color: var(--el-color-danger); } .run { color: var(--el-color-primary); } }
.subtask-bubble__body { padding: 4px 12px 12px; border-top: 1px solid var(--el-border-color-lighter); }
.subtask-bubble__loading { padding: 12px; color: var(--el-text-color-secondary); font-size: 12px; }
.subtask-bubble__msg { padding: 8px 0; }
.subtask-bubble__role {
  font-size: 12px; font-weight: 600; color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.subtask-bubble__thinking { margin: 4px 0; }
/* 展开态限高内滚（同 .ai-thinking 的理由）：子代理长推理不淹没页面 */
.subtask-bubble__thinking :deep(.elx-thinking__content pre) {
  max-height: 280px;
  overflow-y: auto;
}
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
