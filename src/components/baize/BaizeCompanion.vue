<template>
  <!-- 半透明悬浮数字人：Live2D（模型可用时）或 SVG 卡通少女（降级） -->
  <div v-if="!hidden" class="baize-companion" :class="{ minimized, expanded }"
       data-test="baize-companion" :style="positionStyle"
       @pointerdown.prevent="onDragStart">
    <!-- 形象本体 -->
    <div class="companion__stage" data-test="baize-stage" @click="toggleChat">
      <canvas v-if="live2dReady" ref="l2dCanvas" class="companion__l2d" width="300" height="360" />
      <div v-else class="companion__avatar" aria-hidden="true">
        <svg viewBox="0 0 120 150" class="avatar-svg">
          <defs>
            <linearGradient id="bc-hair" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#7c9cff" />
              <stop offset="1" stop-color="#a78bfa" />
            </linearGradient>
          </defs>
          <!-- 双马尾 -->
          <ellipse cx="18" cy="62" rx="12" ry="30" fill="url(#bc-hair)" />
          <ellipse cx="102" cy="62" rx="12" ry="30" fill="url(#bc-hair)" />
          <!-- 头 -->
          <circle cx="60" cy="55" r="34" fill="#ffe8d9" />
          <!-- 刘海 -->
          <path d="M26 48 Q34 18 60 20 Q86 18 94 48 Q80 38 60 40 Q40 38 26 48Z" fill="url(#bc-hair)" />
          <!-- 眼睛（眨眼动画） -->
          <g class="avatar-eyes">
            <ellipse class="eye" cx="47" cy="56" rx="4.5" ry="6" fill="#2c3e66" />
            <ellipse class="eye" cx="73" cy="56" rx="4.5" ry="6" fill="#2c3e66" />
            <circle cx="48.5" cy="54" r="1.6" fill="#fff" />
            <circle cx="74.5" cy="54" r="1.6" fill="#fff" />
          </g>
          <!-- 腮红 -->
          <ellipse cx="40" cy="66" rx="5" ry="3" fill="#ffb3ba" opacity="0.7" />
          <ellipse cx="80" cy="66" rx="5" ry="3" fill="#ffb3ba" opacity="0.7" />
          <!-- 嘴（说话动画切换） -->
          <path class="mouth-idle" d="M55 72 Q60 76 65 72" stroke="#d4737e" stroke-width="2"
                fill="none" stroke-linecap="round" />
          <ellipse class="mouth-talk" cx="60" cy="73" rx="5" ry="4" fill="#d4737e" />
          <!-- 身体（裙装） -->
          <path d="M40 88 Q60 82 80 88 L90 132 L30 132Z" fill="#5c9dff" />
          <path d="M40 88 Q60 96 80 88 L84 104 Q60 112 36 104Z" fill="#e8f1ff" />
          <!-- 白泽徽记 -->
          <circle cx="60" cy="106" r="6" fill="#fff" opacity="0.9" />
          <text x="60" y="110" text-anchor="middle" font-size="9" fill="#4a6cf7"
                font-family="serif">泽</text>
        </svg>
      </div>
      <!-- 气泡提示（主动指导） -->
      <div v-if="tip" class="companion__tip" data-test="baize-tip" @click.stop>
        <span>{{ tip }}</span>
        <span class="companion__tip-close" @click.stop="dismissTip">✕</span>
      </div>
    </div>

    <!-- 对话面板（点击形象展开） -->
    <div v-if="expanded" class="companion__panel" data-test="baize-panel" @click.stop>
      <div class="panel__head">
        <span>白泽小助手</span>
        <span class="panel__close" @click.stop="expanded = false">✕</span>
      </div>
      <div ref="msgBox" class="panel__msgs" data-test="baize-messages">
        <p v-if="!msgs.length" class="panel__empty">
          你好，我是白泽小助手 ✨<br />点我看使用指导，或直接问我怎么用～
        </p>
        <div v-for="(m, i) in msgs" :key="i" class="panel__msg" :class="'is-' + m.role">
          <span>{{ m.text }}</span>
        </div>
        <div v-if="pending" class="panel__msg is-assistant is-pending">
          <span>思考中…</span>
        </div>
      </div>
      <div class="panel__input">
        <input v-model="draft" data-test="baize-input" placeholder="问我怎么用…"
               :disabled="pending" @keydown.enter.prevent="send" />
        <button data-test="baize-send" :disabled="pending || !draft.trim()" @click="send">发送</button>
      </div>
    </div>

    <!-- 最小化悬浮球 -->
    <button v-if="minimized" class="companion__fab" data-test="baize-fab" @click.stop="restore">
      泽
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { createSession, sendMessage, createEventStream } from '@/api/aiChat'

const MIN_KEY = 'baize-companion:minimized'
const OFF_KEY = 'baize-companion:guidance-off'
const SID_KEY = 'baize-companion:session'

const minimized = ref(localStorage.getItem(MIN_KEY) === '1')
const expanded = ref(false)
const live2dReady = ref(false)
const pending = ref(false)
const draft = ref('')
const msgs = ref<Array<{ role: 'user' | 'assistant'; text: string }>>([])
const tip = ref('')
const l2dCanvas = ref<HTMLCanvasElement | null>(null)
const msgBox = ref<HTMLElement | null>(null)

const pos = ref({ x: 24, y: 24 })
const route = useRoute()
// /ai-chat 全屏对话页本身就是 AI 主入口，悬浮数字人在此冗余且会遮挡
// 发送按钮——该路由下整体隐藏
const hidden = computed(() => route.path.startsWith('/ai-chat'))
const positionStyle = computed(() => ({
  right: pos.value.x + 'px',
  bottom: pos.value.y + 'px',
}))

let es: { close(): void } | null = null
let sessionId: string | null = localStorage.getItem(SID_KEY) || null

function restore() {
  minimized.value = false
  localStorage.setItem(MIN_KEY, '0')
}
watch(minimized, v => localStorage.setItem(MIN_KEY, v ? '1' : '0'))

// ── 拖拽（pointer 事件，边界钳制） ───────────────────────────────────────
let drag: { sx: number; sy: number; ox: number; oy: number } | null = null
function onDragStart(e: PointerEvent) {
  if ((e.target as HTMLElement).closest('[data-test="baize-panel"]')) return
  drag = { sx: e.clientX, sy: e.clientY, ox: pos.value.x, oy: pos.value.y }
  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', onDragEnd)
}
function onDragMove(e: PointerEvent) {
  if (!drag) return
  const dx = drag.sx - e.clientX
  const dy = drag.sy - e.clientY
  pos.value = {
    x: Math.min(Math.max(8, drag.ox + dx), window.innerWidth - 120),
    y: Math.min(Math.max(8, drag.oy + dy), window.innerHeight - 140),
  }
}
function onDragEnd() {
  drag = null
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragEnd)
}

// ── 白泽大脑接入：专属会话 + SSE 流式 ───────────────────────────────────
async function ensureSession() {
  if (sessionId) return sessionId
  const s = await createSession()
  sessionId = s.id
  localStorage.setItem(SID_KEY, s.id)
  return s.id
}
function openStream(sid: string) {
  es?.close()
  es = createEventStream(sid, {
    onEvent: ({ event, data }) => {
      const d = data as any
      if (event === 'message.part.updated' && d?.part?.type === 'text') {
        const t = String(d.part.text || '')
        if (t) {
          pending.value = false
          setOrAppendAssistant(t)
        }
      }
    },
    onError: () => {},
  })
}
function setOrAppendAssistant(text: string) {
  const last = msgs.value[msgs.value.length - 1]
  if (last?.role === 'assistant') last.text = text
  else msgs.value.push({ role: 'assistant', text })
  scrollBottom()
}
function scrollBottom() {
  nextTick(() => msgBox.value?.scrollTo({ top: msgBox.value.scrollHeight }))
}

async function send() {
  const text = draft.value.trim()
  if (!text || pending.value) return
  draft.value = ''
  msgs.value.push({ role: 'user', text })
  pending.value = true
  scrollBottom()
  try {
    const sid = await ensureSession()
    if (!es) openStream(sid)
    await sendMessage(sid, text, [], '', 'baize-companion', [])
  } catch {
    pending.value = false
    msgs.value.push({ role: 'assistant', text: '（连接失败，请稍后重试）' })
  }
  scrollBottom()
}

function toggleChat() {
  if (minimized.value) { restore(); return }
  expanded.value = !expanded.value
  if (expanded.value) ensureSession().then(openStream)
  tip.value = ''
}
function dismissTip() {
  tip.value = ''
  localStorage.setItem(OFF_KEY, '1')
}

// ── 主动指导（路由映射 + 节流：同页同提示每日一次 + 可全局关闭） ─────────
const TIPS: Array<[RegExp, string]> = [
  [/^\/(home)?$/, '这是工作台首页，点「AI 助手」即可与我深度对话～'],
  [/^\/ai-chat/, '在这里与我长对话；我也能调用平台工具查数据、跑脚本哦'],
  [/^\/admin\/query/, '数据查询页可以直接写 Mongo 查询，也可以让我用自然语言帮你查'],
  [/^\/admin\/ai-chat/, '管理会话：可对任意会话发起轨迹分析，定位失败原因'],
  [/^\/admin\/ai-skillopt/, 'SkillOpt 看板：技能调用的完成率与版本对比都在这里'],
  [/^\/admin/, '设置中心的所有配置都支持权限细分，改动会记操作日志'],
]
let lastTipKey = localStorage.getItem('baize-companion:last-tip') || ''
function showGuidance() {
  if (localStorage.getItem(OFF_KEY) === '1' || expanded.value || minimized.value) return
  const path = route.path
  const key = path + ':' + new Date().toISOString().slice(0, 10)
  if (key === lastTipKey) return
  const hit = TIPS.find(([re]) => re.test(path))
  if (!hit) return
  lastTipKey = key
  localStorage.setItem('baize-companion:last-tip', key)
  tip.value = hit[1]
  setTimeout(() => { if (tip.value === hit[1]) tip.value = '' }, 12_000)
}

// ── Live2D 加载（模型地址可配；失败优雅降级为 SVG 形象） ─────────────────
async function tryLoadLive2D() {
  const modelUrl = (import.meta as any).env?.VITE_LIVE2D_MODEL_URL
  if (!modelUrl) return
  try {
    const PIXI = await import('pixi.js')
    const { Live2DModel } = await import('pixi-live2d-display')
    ;(window as any).PIXI = PIXI
    const app = new PIXI.Application({
      view: l2dCanvas.value!, width: 300, height: 360, backgroundAlpha: 0,
    })
    const model = await Live2DModel.from(modelUrl)
    app.stage.addChild(model as any)
    const scale = Math.min(300 / model.width, 360 / model.height)
    model.scale.set(scale)
    live2dReady.value = true
  } catch (e) {
    console.warn('[baize-companion] Live2D 不可用，使用 SVG 形象降级', e)
  }
}

onMounted(() => {
  void tryLoadLive2D()
  showGuidance()
})
watch(() => route.path, () => showGuidance())
onUnmounted(() => {
  es?.close()
})
</script>

<style scoped>
.baize-companion {
  position: fixed;
  z-index: 2000;
  right: 24px;
  bottom: 24px;
  opacity: 0.92;
  transition: opacity 0.2s;
}
.baize-companion:hover { opacity: 1; }
/* 最小化时只隐藏形象与面板，保留悬浮球 */
.baize-companion.minimized .companion__stage,
.baize-companion.minimized .companion__panel { display: none; }

.companion__stage { position: relative; width: 300px; height: 360px;
  display: flex; align-items: flex-end; justify-content: center;
  cursor: pointer; }
.companion__l2d { pointer-events: none; }

.companion__avatar { width: 150px; height: 188px;
  filter: drop-shadow(0 6px 14px rgba(30, 58, 138, 0.35));
  animation: bc-float 3.2s ease-in-out infinite; }
@keyframes bc-float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
.avatar-eyes { transform-origin: 60px 56px; animation: bc-blink 4.2s infinite; }
@keyframes bc-blink { 0%,94%,100% { transform: scaleY(1); } 96% { transform: scaleY(0.1); } }
.companion__stage.talking .mouth-idle { display: none; }
.companion__stage.talking .mouth-talk { display: none; }
.avatar-svg .mouth-talk { display: none; }

.companion__tip {
  position: absolute; right: 130px; bottom: 300px;
  max-width: 260px; padding: 8px 12px;
  background: #fff; border: 1px solid var(--el-color-primary-light-7);
  border-radius: 10px; font-size: 12.5px; color: var(--el-text-color-primary);
  box-shadow: 0 4px 12px rgba(0,0,0,0.12); cursor: default;
}
.companion__tip::after { content: ''; position: absolute; right: -6px; bottom: 14px;
  width: 10px; height: 10px; background: #fff; transform: rotate(45deg);
  border-right: 1px solid var(--el-color-primary-light-7);
  border-bottom: 1px solid var(--el-color-primary-light-7); }
.companion__tip-close { margin-left: 8px; cursor: pointer; color: var(--el-text-color-secondary); }

.companion__panel {
  position: absolute; right: 0; bottom: 300px;
  width: 320px; height: 420px;
  background: #fff; border-radius: 14px;
  border: 1px solid var(--el-border-color-light);
  box-shadow: 0 8px 30px rgba(0,0,0,0.18);
  display: flex; flex-direction: column; overflow: hidden; cursor: default;
}
.panel__head { display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; background: linear-gradient(90deg,#1e3a8a,#0e7490);
  color: #fff; font-size: 13px; font-weight: 600; }
.panel__close { cursor: pointer; }
.panel__msgs { flex: 1; overflow-y: auto; padding: 10px 12px; }
.panel__empty { color: var(--el-text-color-secondary); font-size: 12.5px;
  text-align: center; margin-top: 30px; }
.panel__msg { max-width: 85%; margin-bottom: 8px; padding: 7px 10px;
  border-radius: 10px; font-size: 12.5px; white-space: pre-wrap;
  word-break: break-word; }
.panel__msg.is-user { margin-left: auto; background: var(--el-color-primary);
  color: #fff; }
.panel__msg.is-assistant { background: var(--el-fill-color-light); }
.panel__msg.is-pending { color: var(--el-text-color-secondary); }
.panel__input { display: flex; gap: 8px; padding: 8px 10px;
  border-top: 1px solid var(--el-border-color-lighter); }
.panel__input input { flex: 1; border: 1px solid var(--el-border-color);
  border-radius: 8px; padding: 6px 10px; font-size: 12.5px; outline: none; }
.panel__input button { border: 0; border-radius: 8px; padding: 6px 14px;
  background: var(--el-color-primary); color: #fff; cursor: pointer;
  font-size: 12.5px; }
.panel__input button:disabled { opacity: 0.5; cursor: not-allowed; }

.companion__fab { width: 56px; height: 56px; border-radius: 50%;
  border: 2px solid #38bdf8; background: linear-gradient(135deg,#1e3a8a,#0e7490);
  color: #e0f2fe; font-size: 22px; font-family: serif; cursor: pointer;
  box-shadow: 0 4px 14px rgba(14, 116, 144, 0.4); }
</style>
