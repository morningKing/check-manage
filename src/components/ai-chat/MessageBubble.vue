<script setup lang="ts">
/**
 * 单条消息气泡（从 AiChatView 拆出的渲染隔离组件）。
 *
 * 为什么拆：AiChatView 是 3000+ 行单组件，消息流任何响应式变化（打开会话时
 * ~10 个 fire-and-forget 请求各自落库、流式 delta、usage 更新…）都会重跑整个
 * 组件的 render，模板里的 mergeReasoningParts/splitArtifacts/parseTodos×2/
 * parseQueryResult×2/parseQuestionPart×2（含 JSON.parse）随之对**全部消息**
 * 重算——重度会话单次全量重渲染实测 ~2.2s 主线程冻结。拆出后：
 * - 每条消息的重解析收敛进 prepared computed，只在本消息变化时重算一次
 *   （消灭模板里的双份解析）；
 * - 其余消息的子树被 Vue 按 props 等值跳过，渲染开销从 O(全部消息) 降到
 *   O(变化的消息)。
 * 交互（复制/编辑/重试/反馈/artifact 预览运行/子代理拉取）全部以回调 props
 * 透传回父级——父级传入的是 setup 顶层声明的函数引用（稳定），子组件不会因
 * 父组件重渲染而被迫更新。
 */
import { computed } from 'vue'
import { ElIcon, ElImage } from 'element-plus'
import { Clock, CopyDocument, EditPen, RefreshRight, WarningFilled } from '@element-plus/icons-vue'
import { Bubble, Thinking } from 'vue-element-plus-x'
import MarkdownView from './MarkdownView.vue'
import ToolCallBubble from './ToolCallBubble.vue'
import TodoListBlock from './TodoListBlock.vue'
import QuestionResultCard from './QuestionResultCard.vue'
import ArtifactCard from './ArtifactCard.vue'
import type { ArtifactVersion } from './ArtifactPreview.vue'
import RunResultBlock from './RunResultBlock.vue'
import McpServicesBlock from './McpServicesBlock.vue'
import LspFormatterBlock from './LspFormatterBlock.vue'
import SubtaskBubble from './SubtaskBubble.vue'
import ChatFile from './ChatFile.vue'
import QueryResultBlock from './QueryResultBlock.vue'
import UserMentionText from './UserMentionText.vue'
import { mergeReasoningParts, splitArtifacts, type CodeSegment, type Segment } from '@/utils/artifacts'
import { parseTodos, type TodoItem } from '@/utils/todos'
import { parseQuestionPart } from '@/utils/questionPart'
import { summarizeMeta } from '@/utils/aiMeta'
import { downloadFileUrl, getSubtaskMessages, type AiMessage, type AiContentPart, type AiFile } from '@/api/aiChat'

const props = defineProps<{
  message: AiMessage
  /** 所属会话 id（文件下载 URL 需要） */
  sessionId: string
  /** 回合进行中：编辑/重试按钮禁用 */
  streaming: boolean
  /** @文件路径 提及渲染的候选文件（上传 + 产出） */
  mentionFiles: AiFile[]
  /** 正在运行的 artifact 代码（卡片逐卡转圈） */
  runningCode: string | null
  /** artifact 版本计数（父级 versionMap：同名文件跨回合归并） */
  getVersions: (seg: CodeSegment, idx: number) => ArtifactVersion[]
  /** 子代理轨迹拉取（展开才拉，模块级稳定引用） */
  fetchSubtaskMessages: typeof getSubtaskMessages
  onPreview: (seg: CodeSegment, idx: number) => void
  onRun: (seg: CodeSegment, idx: number) => void
  onMentionPreview: (path: string) => void
  onCopy: (m: AiMessage) => void
  onEdit: (m: AiMessage) => void
  onRetryUser: (m: AiMessage) => void
  onRetryAssistant: (m: AiMessage) => void
  onFeedback: (m: AiMessage, next: 1 | -1) => void
}>()

const fileUrl = (path: string) => downloadFileUrl(props.sessionId, path)

function isRunResultOnly(m: AiMessage): boolean {
  return m.content.length > 0 && m.content.every(p => p.type === 'run_result')
}

function parseQueryResult(p: any): any | null {
  if (p?.name !== 'query_collection') return null
  let r: any = p.result
  if (typeof r === 'string') {
    try { r = JSON.parse(r) } catch { return null }
  }
  return r && typeof r === 'object' && typeof r.mode === 'string' ? r : null
}

function messageText(m: AiMessage): string {
  return m.content
    .filter((p) => p.type === 'text' && (p as any).text)
    .map((p) => (p as any).text as string)
    .join('\n')
}

/** 每条消息一次性的重解析结果：mergeReasoningParts + 各 part 类型解析 +
 *  assistant 文本的 artifact 切分，全部缓存——消息不变不重算。模板里原来
 *  每次渲染对每个 part 调 2 遍（v-if 判断 + prop 各一遍）且全消息重算，
 *  这是打开重度会话卡顿的大头之一。 */
interface PreparedPart {
  part: AiContentPart
  todos: TodoItem[] | null
  query: any
  question: ReturnType<typeof parseQuestionPart>
  /** 仅 assistant 文本 part：splitArtifacts 切分结果（text/code 段联合） */
  segs: Segment[] | null
}
const prepared = computed<PreparedPart[]>(() =>
  mergeReasoningParts(props.message.content).map((p) => ({
    part: p,
    todos: p.type === 'tool_use' ? parseTodos(p) : null,
    query: p.type === 'tool_use' ? parseQueryResult(p) : null,
    question: p.type === 'tool_use' ? parseQuestionPart(p) : null,
    segs: p.type === 'text' && props.message.role === 'assistant' && p.text
      ? splitArtifacts(p.text)
      : null,
  })))
</script>

<template>
  <div v-if="message.role !== 'user' && !isRunResultOnly(message)" class="msg__role">AI 助手</div>
  <Bubble
    :placement="message.role === 'user' ? 'end' : 'start'"
    :variant="message.role === 'user' ? 'filled' : 'borderless'"
    :class="['ai-bubble', 'ai-bubble--' + message.role]"
  >
    <template #content>
      <div v-if="message.queued" class="msg__queued-tag">
        <ElIcon><Clock /></ElIcon> 排队中 · 回合结束后自动发送
      </div>
      <template v-for="(rp, i) in prepared" :key="i">
        <ChatFile v-if="rp.part.type === 'file'" :name="rp.part.name" :src="fileUrl(rp.part.path)" />
        <template v-else-if="rp.part.type === 'tool_use'">
          <TodoListBlock
            v-if="rp.todos"
            :todos="rp.todos"
          />
          <QueryResultBlock
            v-else-if="rp.query"
            :result="rp.query" :download-url="fileUrl"
          />
          <!-- question 工具完成后保留卡片渲染（勾选你的选择），
               而不是折叠成 JSON 工具气泡；运行中只留一条占位提示，
               交互卡片在对话流末尾。 -->
          <QuestionResultCard
            v-else-if="rp.question"
            :view="rp.question"
          />
          <ToolCallBubble
            v-else
            :name="rp.part.name" :title="rp.part.title" :status="rp.part.status"
            :input="rp.part.input" :result="rp.part.result" :duration-ms="rp.part.durationMs"
          />
        </template>
        <RunResultBlock
          v-else-if="rp.part.type === 'run_result'"
          :result="rp.part" :download-url="fileUrl"
        />
        <McpServicesBlock
          v-else-if="rp.part.type === 'mcp_services'"
          :servers="rp.part.servers"
        />
        <LspFormatterBlock
          v-else-if="rp.part.type === 'lsp_formatter'"
          :lsp="rp.part.lsp" :formatters="rp.part.formatters" :error="rp.part.error"
        />
        <Thinking
          v-else-if="rp.part.type === 'reasoning' && rp.part.text"
          class="ai-thinking"
          :content="rp.part.text" status="end"
          :model-value="false"
        />
        <SubtaskBubble
          v-else-if="rp.part.type === 'subtask_use'"
          :subtask-id="rp.part.subtaskId" :session-id="sessionId"
          :agent="rp.part.agent" :description="rp.part.description" :status="rp.part.status"
          :depth="1" :segment-count="rp.part.segmentCount" :fetch-fn="fetchSubtaskMessages"
        />
        <!-- 回合级失败（session.error / 出错的 assistant 消息）：
             持久化为 error part，刷新后仍在，见 chat_persist.py -->
        <div v-else-if="rp.part.type === 'error'" class="msg__turn-error">
          <ElIcon><WarningFilled /></ElIcon>
          <span>{{ rp.part.text }}</span>
        </div>
        <template v-else-if="rp.part.type === 'text' && rp.part.text">
          <!-- assistant: lift big code/doc blocks into artifact cards -->
          <template v-if="message.role === 'assistant'">
            <template v-for="(seg, si) in rp.segs" :key="si">
              <MarkdownView v-if="seg.type === 'text' && seg.text.trim()" :text="seg.text" />
              <ArtifactCard
                v-else-if="seg.type === 'code'"
                :lang="seg.lang" :code="seg.code" :index="si"
                :versions="getVersions(seg, si).length"
                :running="runningCode === seg.code"
                @preview="onPreview(seg, si)"
                @run="onRun(seg, si)"
              />
            </template>
          </template>
          <!-- user: @文件路径 renders as a clickable chip (F2) -->
          <UserMentionText
            v-else
            :text="rp.part.text"
            :files="mentionFiles"
            @preview="onMentionPreview"
          />
        </template>
        <!-- user 气泡：image part（图片附件，后端 stored parts type=image）渲染为可预览图片 -->
        <template v-else-if="rp.part.type === 'image'">
          <ElImage
            class="msg-image" :src="downloadFileUrl(sessionId, rp.part.path)"
            :alt="rp.part.name" fit="cover"
            :preview-src-list="[downloadFileUrl(sessionId, rp.part.path)]" hide-on-click-modal
          />
        </template>
      </template>
    </template>
  </Bubble>
  <div v-if="message.role === 'assistant' && summarizeMeta(message.meta)" class="msg__meta">
    {{ summarizeMeta(message.meta) }}
  </div>
  <div v-if="messageText(message)" class="msg__actions" :class="`msg__actions--${message.role}`">
    <button
      class="msg__action-btn" type="button" title="复制" aria-label="复制"
      @click="onCopy(message)"
    ><ElIcon><CopyDocument /></ElIcon></button>
    <template v-if="message.role === 'user'">
      <button
        class="msg__action-btn" type="button" title="编辑并重发" aria-label="编辑并重发"
        :disabled="streaming" @click="onEdit(message)"
      ><ElIcon><EditPen /></ElIcon></button>
      <button
        class="msg__action-btn" type="button" title="重新生成" aria-label="重新生成"
        :disabled="streaming" @click="onRetryUser(message)"
      ><ElIcon><RefreshRight /></ElIcon></button>
    </template>
    <template v-else-if="message.role === 'assistant'">
      <button
        class="msg__action-btn" type="button" title="重新生成" aria-label="重新生成"
        :disabled="streaming" @click="onRetryAssistant(message)"
      ><ElIcon><RefreshRight /></ElIcon></button>
      <button class="msg__action-btn" :class="{ 'feedback-on': message.rating === 1 }"
        type="button" data-test="feedback-up" title="赞" aria-label="赞"
        @click="onFeedback(message, 1)"
      ><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg></button>
      <button class="msg__action-btn" :class="{ 'feedback-on': message.rating === -1 }"
        type="button" data-test="feedback-down" title="踩" aria-label="踩"
        @click="onFeedback(message, -1)"
      ><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1 2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/></svg></button>
    </template>
  </div>
</template>

<!-- 样式随模板从 AiChatView 迁入（scoped 规则只匹配带本组件 scope 属性的
     元素，留在父组件会失配）。`.msg:hover .msg__actions` 的 .msg 是父级渲染
     的祖先壳，scoped 编译只给最后一个选择器加属性，仍能命中。 -->
<style scoped lang="scss">
.msg__role {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
/* 运行中插话的排队标记（气泡内顶部一行小字，发送后随 queued 标记消失） */
.msg__queued-tag {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
/* 回合级失败提示条（error part）：出错也要看得见，而不是悄悄停转 */
.msg__turn-error {
  display: flex;
  align-items: flex-start;
  gap: 6px;
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
.msg__meta {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-family: var(--el-font-family-mono, monospace);
}
.msg__actions {
  display: flex; gap: 4px;
  margin-top: 4px;
  opacity: 0;
  transition: opacity 0.15s ease;
  width: fit-content;
}
.msg__actions--assistant { margin-left: 0; }
.msg:hover .msg__actions { opacity: 0.9; }
.msg__action-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px;
  padding: 0;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color);
  color: var(--el-text-color-regular);
  font-size: 13px;
  cursor: pointer;
}
.msg__action-btn:hover:not(:disabled) {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}
.msg__action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.feedback-on { color: var(--el-color-primary); }
.ai-bubble--user :deep(.elx-bubble__content-wrapper),
.ai-bubble--user :deep(.elx-bubble__content) {
  background: var(--el-fill-color) !important;
  color: var(--el-text-color-primary) !important;
  border: none !important;
  border-radius: 14px !important;
}
/* Assistant: borderless, full width, document feel — no frame around content */
.ai-bubble--assistant { width: 100%; }
.ai-bubble--assistant :deep(.elx-bubble__content-wrapper),
.ai-bubble--assistant :deep(.elx-bubble__content) {
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
  padding: 0 !important;
  /* Full width (not shrink-to-content) so 100%-width children like echarts
     charts don't collapse to a tiny default size. */
  width: 100% !important;
  max-width: 100% !important;
}
.ai-bubble--assistant :deep(.md-editor-preview) { font-size: 15px; line-height: 1.7; }
/* 展开态限高内滚：数千行推理会把会话撑到几千像素高，收起按钮（块头部）
   也被顶出视野。限高后头部始终近在眼前，内容在块内滚动。
   （AiChatView 的流式思考块也用同名类，两边各留一份规则。） */
.ai-thinking { max-width: 780px; margin: 0 auto 24px; }
.ai-thinking :deep(.elx-thinking__content pre) {
  max-height: 320px;
  overflow-y: auto;
}
.msg-image { max-width: 240px; max-height: 180px; border-radius: 8px; }
</style>
