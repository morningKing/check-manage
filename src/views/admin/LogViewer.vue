<template>
  <div class="log-viewer" data-test="log-viewer">
    <div class="log-viewer__bar">
      <el-radio-group v-model="activeFile" size="small" @change="switchFile">
        <el-radio-button data-test="file-chat" value="chat">ai-chat.log</el-radio-button>
        <el-radio-button data-test="file-serve" value="serve">oc-serve.log</el-radio-button>
      </el-radio-group>
      <!-- data-test 必须挂在包裹层：ElInput 的 $attrs 会落到内部 <input>，
           [data-test="filter-input"] input 这类后代选择器需要外层宿主。 -->
      <div class="log-viewer__filter" data-test="filter-input">
        <el-input v-model="filter" size="small" clearable
                  placeholder="关键字筛选（等价 grep）" />
      </div>
      <el-button size="small" data-test="pause-btn" @click="togglePause">
        {{ paused ? '继续' : '暂停' }}</el-button>
      <el-switch v-model="autoScroll" size="small" active-text="自动滚动" />
      <el-button size="small" data-test="clear-btn" @click="clearView">清屏</el-button>
      <span class="log-viewer__status">
        <span :class="['log-viewer__dot', `is-${status}`]" />
        {{ statusText }} · 命中 {{ matchedCount }} / 共 {{ lineCount }} 行
      </span>
    </div>
    <div ref="paneRef" class="log-viewer__pane" data-test="log-pane" @scroll="onScroll">
      <div v-if="trimmed" class="log-viewer__marker">— 已裁剪更早日志 —</div>
      <div v-for="(e, i) in visibleEntries" :key="i"
           :class="['log-viewer__line', e.kind === 'marker' ? 'is-marker' : '']">
        {{ e.text }}</div>
      <div v-if="!visibleEntries.length" class="log-viewer__empty">
        {{ status === 'waiting' ? '暂无日志文件（等待产生）' : '（无匹配行）' }}</div>
    </div>
    <el-button v-if="!autoScroll" class="log-viewer__to-bottom" size="small"
               data-test="to-bottom" @click="backToBottom">回到底部</el-button>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElButton, ElInput, ElRadioButton, ElRadioGroup, ElSwitch } from 'element-plus'
import { fetchLogTail } from '@/api/adminLogs'

/**
 * 运行日志跟尾页（2026-10-14 spec §5）：1.5s 增量轮询 + 暂停/自动滚动/筛选。
 * 两个常量与轮询契约对齐 spec §4/§5，改动需两侧同步。
 */
const POLL_MS = 1500
const BUFFER_MAX = 5000
const TRIM_BATCH = 1000
const DEFAULT_LIMIT = 300

interface LogEntry { kind: 'line' | 'marker'; text: string }

const activeFile = ref<'chat' | 'serve'>('chat')
const entries = ref<LogEntry[]>([])
const offsetRef = ref(-1)
const paused = ref(false)
const autoScroll = ref(true)
const filter = ref('')
const status = ref<'ok' | 'error' | 'waiting'>('ok')
const trimmed = ref(false)
const paneRef = ref<HTMLElement | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

const lineEntries = computed(() => entries.value.filter(e => e.kind === 'line'))
const lineCount = computed(() => lineEntries.value.length)
const matchedCount = computed(() => {
  const kw = filter.value.trim().toLowerCase()
  if (!kw) return lineCount.value
  return lineEntries.value.filter(e => e.text.toLowerCase().includes(kw)).length
})
const visibleEntries = computed(() => {
  const kw = filter.value.trim().toLowerCase()
  return entries.value.filter(e =>
    e.kind === 'marker' || !kw || e.text.toLowerCase().includes(kw))
})
const statusText = computed(() =>
  status.value === 'ok' ? '实时' : status.value === 'waiting' ? '等待文件' : '连接中断')

function scrollToBottom() {
  void nextTick(() => {
    const el = paneRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function appendLines(lines: string[]) {
  if (!lines.length) return
  for (const text of lines) entries.value.push({ kind: 'line', text })
  if (entries.value.length > BUFFER_MAX) {
    entries.value.splice(0, TRIM_BATCH)
    trimmed.value = true
  }
  if (autoScroll.value) scrollToBottom()
}

async function poll() {
  if (document.hidden || paused.value) return
  try {
    const r = await fetchLogTail(activeFile.value, offsetRef.value, DEFAULT_LIMIT)
    if (r.missing) {
      status.value = 'waiting'
      return
    }
    status.value = 'ok'
    if (r.rotated && offsetRef.value >= 0) {
      // 轮转/截断：清缓冲、留分隔线，再把新文件的尾窗行补进来
      entries.value = [{
        kind: 'marker',
        text: `— 文件已轮转，重新加载（${new Date().toLocaleTimeString()}） —`,
      }]
      trimmed.value = false
    }
    offsetRef.value = r.offset
    appendLines(r.lines)
  } catch {
    status.value = 'error'
  }
}

function stopTimer() {
  if (timer) { clearInterval(timer); timer = null }
}
function startTimer() {
  if (!timer) timer = setInterval(() => { void poll() }, POLL_MS)
}

function togglePause() {
  paused.value = !paused.value
  if (paused.value) stopTimer()
  else { startTimer(); void poll() }
}

function switchFile() {
  stopTimer()
  offsetRef.value = -1
  entries.value = []
  trimmed.value = false
  status.value = 'ok'
  if (!paused.value) { startTimer(); void poll() }
}

function clearView() {
  entries.value = []
  trimmed.value = false
}

function backToBottom() {
  autoScroll.value = true
  scrollToBottom()
}

function onScroll() {
  const el = paneRef.value
  if (!el) return
  const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 24
  autoScroll.value = nearBottom
}

function onVisibility() {
  if (document.hidden) stopTimer()
  else if (!paused.value) { startTimer(); void poll() }
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility)
  startTimer()
  void poll()
})
onUnmounted(() => {
  document.removeEventListener('visibilitychange', onVisibility)
  stopTimer()
})
</script>

<style scoped lang="scss">
.log-viewer {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 400px;
}
.log-viewer__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.log-viewer__filter { width: 220px; }
.log-viewer__status {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.log-viewer__dot {
  width: 8px; height: 8px; border-radius: 50%; background: #67c23a;
  &.is-error { background: #f56c6c; }
  &.is-waiting { background: #e6a23c; }
}
.log-viewer__pane {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #1e1e1e;
  color: #d4d4d4;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
  padding: 8px 10px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-viewer__line.is-marker {
  color: #e6a23c;
  text-align: center;
  margin: 4px 0;
}
.log-viewer__empty {
  color: #666;
  text-align: center;
  padding-top: 40px;
}
.log-viewer__to-bottom {
  position: absolute;
  right: 24px;
  bottom: 24px;
}
</style>
