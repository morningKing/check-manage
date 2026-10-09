<template>
  <div class="diag-list" data-test="diag-list">
    <h4>优化诊断</h4>
    <ElAlert v-if="!diagnoses.length" type="success" :closable="false"
             title="未发现明显耗时问题" />
    <div v-for="(d, i) in diagnoses" :key="i"
         class="diag" :class="`diag--${d.severity}`">
      <ElTag size="small" :type="d.severity === 'warn' ? 'warning' : 'info'">
        {{ d.severity === 'warn' ? '建议优化' : '说明' }}
      </ElTag>
      <span class="diag__text">{{ d.text }}</span>
      <ElButton v-if="anchorTarget(d)" link size="small" data-test="diag-locate"
                @click="locate(d)">定位</ElButton>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onUnmounted } from 'vue'
import { ElAlert, ElTag, ElButton } from 'element-plus'
import type { Diagnosis } from '@/api/aiSkills'

defineProps<{ diagnoses: Diagnosis[] }>()

// 连续点击防竞态：新点击清掉上一次的摘除定时器（否则 2s 内旧定时器会提前
// 摘掉新一次的 flash）
let flashTimer: ReturnType<typeof setTimeout> | null = null
onUnmounted(() => { if (flashTimer) clearTimeout(flashTimer) })

function anchorTarget(d: Diagnosis): string | null {
  if (d.anchor.type === 'subtask') return `subtask:${d.anchor.ref}`
  if (d.anchor.type === 'turn') return `turn:${d.anchor.ref}`
  if (d.anchor.type === 'segment') return `segment:${d.anchor.ref}`
  return null
}

function locate(d: Diagnosis) {
  // 回退链（对接约定见 PerfTaskDetail 锚点注释）：turn 行锚 → segment 锚 → 逐类型兜底
  const candidates: string[] = []
  if (d.anchor.type === 'turn') candidates.push(`.diag-anchor-turn-${d.anchor.ref}`)
  for (const t of ['segment', 'subtask', 'turn']) {
    candidates.push(`[data-diag-anchor="${t}:${d.anchor.ref}"]`)
  }
  for (const sel of candidates) {
    const el = document.querySelector(sel)
    if (!el) continue
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.remove('diag-flash')
    void (el as HTMLElement).offsetWidth
    el.classList.add('diag-flash')
    if (flashTimer) clearTimeout(flashTimer)
    flashTimer = setTimeout(() => {
      el.classList.remove('diag-flash')
      flashTimer = null
    }, 2000)
    return
  }
}
</script>

<style scoped lang="scss">
.diag { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 6px; margin-bottom: 6px; }
.diag--warn { background: var(--el-color-warning-light-9); }
.diag--info { background: var(--el-fill-color-light); }
.diag__text { flex: 1; font-size: 13px; }
</style>

<!-- diag-flash 挂在被定位的任意元素上（含非本组件的表格行/区块），keyframes
     必须全局可见：scoped 块会重写 keyframes 名而 :global 块里的 animation
     引用不被重写，二者分家会导致动画指向不存在的名字（深色样式事故同款教训：
     主题/全局态覆盖放非 scoped 独立块，显式前缀限定）。 -->
<style lang="scss">
.diag-flash { outline: 2px solid var(--el-color-primary); animation: diagPulse 1s ease 2; }
@keyframes diagPulse { 50% { opacity: 0.55; } }
</style>
