<template>
  <ElDrawer :model-value="true" :title="title" size="64%" @close="emit('close')">
    <div v-loading="loading" class="subtask-perf" data-test="subtask-perf">
      <ElAlert v-if="error" type="error" :closable="false" :title="error" />
      <ElAlert v-else-if="empty" type="info" :closable="false" data-test="perf-empty"
               title="该子任务还没有可分析的执行记录（未跑过，或执行记录未落库）" />
      <template v-else-if="detail">
        <div class="subtask-perf__toolbar">
          <ElButton size="small" type="primary" data-test="copy-md" @click="copyMarkdown">
            复制 Markdown 报告</ElButton>
          <span v-if="detail.tools.callsTruncated" class="muted">
            调用明细超过 {{ CALLS_CAP }} 条已截断（Top 排名基于已载入明细）</span>
        </div>

        <!-- 概要 -->
        <h4>概要</h4>
        <div class="subtask-perf__cards">
          <div class="card"><div class="num">{{ fmtMs(cov.wallMs) }}</div>
            <div class="lbl">墙钟</div></div>
          <div class="card"><div class="num">{{ fmtMs(cov.modelMs) }}</div>
            <div class="lbl">模型 {{ pct(modelRatio) }}</div></div>
          <div class="card"><div class="num">{{ fmtMs(cov.toolMs) }}</div>
            <div class="lbl">工具 {{ pct(toolRatio) }}</div></div>
          <div class="card"><div class="num">{{ fmtMs(cov.subagentWaitMs) }}</div>
            <div class="lbl">子代理等待</div></div>
          <div class="card"><div class="num">{{ fmtMs(cov.idleMs) }}</div>
            <div class="lbl">空闲</div></div>
          <div class="card"><div class="num">{{ detail.attempt.turns }}</div>
            <div class="lbl">模型轮次</div></div>
          <div class="card"><div class="num">{{ callCount }}</div>
            <div class="lbl">工具调用</div></div>
          <div class="card" :class="{ 'is-danger': failedCalls.length > 0 }">
            <div class="num">{{ failedCalls.length }}</div><div class="lbl">失败调用</div></div>
          <div class="card" :class="{ 'is-warn': repeats.length > 0 }">
            <div class="num">{{ repeats.length }}</div><div class="lbl">重复组</div></div>
        </div>
        <div class="subtask-perf__tokens muted">
          tokens：输入 {{ detail.attempt.tokensIn }} / 输出 {{ detail.attempt.tokensOut }}
          <span v-if="detail.completeness?.turnsWithoutDuration">
            · {{ detail.completeness.turnsWithoutDuration }} 轮缺耗时数据（旧数据）</span>
          <span v-if="!detail.tools.durationAvailable">
            · 该任务工具耗时未采集（旧数据），Top 排名不可用</span>
        </div>

        <!-- 最慢工具调用 Top 10 -->
        <h4>最慢工具调用 Top 10 <span class="muted">（含「开子代理」委派等待）</span></h4>
        <ElTable :data="topCalls" size="small" max-height="320" data-test="top-calls">
          <ElTableColumn type="index" width="46" />
          <ElTableColumn label="工具" width="120">
            <template #default="{ row }">
              <span v-if="row.isDelegation" class="muted">开子代理</span>
              <span v-else>{{ row.tool }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn label="耗时" width="90">
            <template #default="{ row }">{{ fmtMs(row.durationMs) }}</template>
          </ElTableColumn>
          <ElTableColumn label="状态" width="80">
            <template #default="{ row }">
              <ElTag size="small" :type="row.state === 'error' ? 'danger' : 'info'">
                {{ row.state }}</ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn label="归属" width="110">
            <template #default="{ row }">{{ ownerOf(row) }}</template>
          </ElTableColumn>
          <ElTableColumn label="参数" min-width="260" show-overflow-tooltip>
            <template #default="{ row }">
              <code class="subtask-perf__args">{{ row.args || '—' }}</code>
            </template>
          </ElTableColumn>
          <ElTableColumn label="时间" width="170">
            <template #default="{ row }">{{ fmtTime(row.startedAt) }}</template>
          </ElTableColumn>
        </ElTable>

        <!-- 推理耗时 Top 10 -->
        <h4>推理耗时 Top 10 <span class="muted">（模型轮次）</span></h4>
        <ElTable :data="topTurns" size="small" max-height="320" data-test="top-turns">
          <ElTableColumn type="index" width="46" />
          <ElTableColumn label="时间" width="170">
            <template #default="{ row }">{{ fmtTime(row.createdAt) }}</template>
          </ElTableColumn>
          <ElTableColumn label="耗时" width="90">
            <template #default="{ row }">{{ fmtMs(row.durationMs) }}</template>
          </ElTableColumn>
          <ElTableColumn label="tokens 进/出" width="130">
            <template #default="{ row }">{{ row.tokensIn }} / {{ row.tokensOut }}</template>
          </ElTableColumn>
          <ElTableColumn label="预览" min-width="260" show-overflow-tooltip
                         prop="preview" />
        </ElTable>

        <!-- 失败工具调用 -->
        <h4>失败工具调用 <span class="muted">（{{ failedCalls.length }} 条）</span></h4>
        <div v-if="!failedCalls.length" class="muted">无失败调用</div>
        <ElTable v-else :data="failedCalls" size="small" max-height="280"
                 data-test="failed-calls">
          <ElTableColumn label="工具" width="120">
            <template #default="{ row }">
              <span v-if="row.isDelegation" class="muted">开子代理</span>
              <span v-else>{{ row.tool }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn label="耗时" width="90">
            <template #default="{ row }">{{ fmtMs(row.durationMs) }}</template>
          </ElTableColumn>
          <ElTableColumn label="归属" width="110">
            <template #default="{ row }">{{ ownerOf(row) }}</template>
          </ElTableColumn>
          <ElTableColumn label="参数" min-width="280" show-overflow-tooltip>
            <template #default="{ row }">
              <code class="subtask-perf__args">{{ row.args || '—' }}</code>
            </template>
          </ElTableColumn>
          <ElTableColumn label="时间" width="170">
            <template #default="{ row }">{{ fmtTime(row.startedAt) }}</template>
          </ElTableColumn>
        </ElTable>

        <!-- 重复调用 -->
        <h4>重复调用 <span class="muted">（同工具 + 同参数 ≥ {{ REPEAT_THRESHOLD }} 次）</span></h4>
        <div v-if="!repeats.length" class="muted">无重复调用</div>
        <ElTable v-else :data="repeats" size="small" max-height="280"
                 data-test="repeat-calls">
          <ElTableColumn prop="tool" label="工具" width="140" />
          <ElTableColumn label="参数" min-width="300" show-overflow-tooltip>
            <template #default="{ row }">
              <code class="subtask-perf__args">{{ row.argsPreview || '—' }}</code>
            </template>
          </ElTableColumn>
          <ElTableColumn label="次数" width="80">
            <template #default="{ row }">×{{ row.count }}</template>
          </ElTableColumn>
          <ElTableColumn label="累计耗时" width="110">
            <template #default="{ row }">
              {{ row.totalMs == null ? '-' : fmtMs(row.totalMs) }}</template>
          </ElTableColumn>
        </ElTable>

        <!-- 子代理下钻 -->
        <h4>子代理分解 <span class="muted">（{{ subagentGroups.length }} 个）</span></h4>
        <div v-if="!subagentGroups.length" class="muted">本次执行没有子代理委派</div>
        <ElCollapse v-else class="subtask-perf__subagents" data-test="subagent-groups">
          <ElCollapseItem v-for="g in subagentGroups" :key="g.subtaskId"
                          :name="g.subtaskId">
            <template #title>
              <span class="subtask-perf__sub-title">
                {{ g.agent || '(未命名)' }}
                <span class="muted">
                  墙钟 {{ fmtMs(g.wallMs) }} · 工具 {{ g.calls.length }}
                  · 失败 {{ g.failed }} · 重复 {{ g.repeats.length }}
                </span>
              </span>
            </template>
            <div v-if="g.description" class="muted subtask-perf__sub-desc">
              委派：{{ g.description }}</div>
            <div class="subtask-perf__sub-grid">
              <div>
                <h5>其最慢调用 Top 5</h5>
                <div v-if="!g.topCalls.length" class="muted">无带耗时的调用</div>
                <ElTable v-else :data="g.topCalls" size="small" max-height="200">
                  <ElTableColumn type="index" width="42" />
                  <ElTableColumn prop="tool" label="工具" width="110" />
                  <ElTableColumn label="耗时" width="84">
                    <template #default="{ row }">{{ fmtMs(row.durationMs) }}</template>
                  </ElTableColumn>
                  <ElTableColumn label="参数" min-width="200" show-overflow-tooltip>
                    <template #default="{ row }">
                      <code class="subtask-perf__args">{{ row.args || '—' }}</code>
                    </template>
                  </ElTableColumn>
                </ElTable>
              </div>
              <div>
                <h5>其推理 Top 5</h5>
                <div v-if="!g.topTurns.length" class="muted">无轮次耗时数据（旧数据）</div>
                <ElTable v-else :data="g.topTurns" size="small" max-height="200">
                  <ElTableColumn type="index" width="42" />
                  <ElTableColumn label="耗时" width="84">
                    <template #default="{ row }">{{ fmtMs(row.durationMs) }}</template>
                  </ElTableColumn>
                  <ElTableColumn label="时间" width="160">
                    <template #default="{ row }">{{ fmtTime(row.createdAt) }}</template>
                  </ElTableColumn>
                </ElTable>
              </div>
            </div>
          </ElCollapseItem>
        </ElCollapse>

        <!-- 运行诊断（O1） -->
        <ElDivider content-position="left">运行诊断（O1）</ElDivider>
        <RunDiagnosisPanel v-if="attemptId" :attempt-id="attemptId" />
      </template>
    </div>
  </ElDrawer>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ElAlert, ElButton, ElCollapse, ElCollapseItem, ElDivider, ElDrawer, ElMessage,
  ElTable, ElTableColumn, ElTag,
} from 'element-plus'
import { getAdminChildPerf } from '@/api/aiBatchAdmin'
import { perfAttempt } from '@/api/aiSkills'
import type {
  PerfAttemptDetail, PerfToolCall, PerfTurn,
} from '@/api/aiSkills'
import { fmtMs, pct } from './format'
import RunDiagnosisPanel from './RunDiagnosisPanel.vue'

/**
 * 子任务性能报告（2026-10-15）：批子会话 / 性能分析页共用的报告抽屉。
 * 数据源二选一：batchId+sid（批任务入口，后端解析子会话最新 attempt）或
 * attemptId（技能拟合优化页入口）。分区：概要 / 最慢工具调用 Top10（含参数）/
 * 推理耗时 Top10 / 失败调用 / 重复调用 / 子代理分解 / 运行诊断（O1，
 * 仅 attemptId 入口）；一键复制 Markdown。
 */

const props = defineProps<{
  batchId?: string
  sid?: string
  attemptId?: string
  title?: string
}>()
const emit = defineEmits<{ (e: 'close'): void }>()

const REPEAT_THRESHOLD = 3      // 与后端 perf_analysis.REPEAT_TOOL_COUNT 对齐
const CALLS_CAP = 500           // 与后端 TOOL_CALLS_MAX 对齐（截断提示用）
const TOP_N = 10

const loading = ref(true)
const error = ref('')
const empty = ref(false)
const detail = ref<PerfAttemptDetail | null>(null)

const title = computed(() => props.title || '子任务性能报告')

const cov = computed(() => detail.value?.coverage ?? {
  wallMs: 0, modelMs: 0, subagentWaitMs: 0, idleMs: 0, toolMs: 0,
})
const modelRatio = computed(() => cov.value.wallMs
  ? cov.value.modelMs / cov.value.wallMs : 0)
const toolRatio = computed(() => cov.value.wallMs
  ? cov.value.toolMs / cov.value.wallMs : 0)

const allCalls = computed<PerfToolCall[]>(() => detail.value?.tools.calls ?? [])
const callCount = computed(() => allCalls.value.length)
const topCalls = computed(() => allCalls.value
  .filter(c => c.durationMs != null)
  .slice()
  .sort((a, b) => (b.durationMs as number) - (a.durationMs as number))
  .slice(0, TOP_N))
const topTurns = computed(() => (detail.value?.turns ?? [])
  .slice()
  .sort((a, b) => (b.durationMs ?? 0) - (a.durationMs ?? 0))
  .slice(0, TOP_N))
const failedCalls = computed(() => allCalls.value.filter(c => c.state === 'error'))
const repeats = computed(() => detail.value?.tools.repeats ?? [])

const agentBySubtask = computed<Record<string, string>>(() => {
  const m: Record<string, string> = {}
  for (const st of detail.value?.subtasks ?? []) {
    m[st.subtaskId] = st.agent || '(未命名)'
  }
  return m
})

function ownerOf(c: PerfToolCall): string {
  return c.subtaskId ? (agentBySubtask.value[c.subtaskId] ?? '子代理') : '主会话'
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false })
}

/** 子代理分组：每个子代理的调用/失败/重复/最慢调用/推理 Top（数据来自
 *  calls.subtaskId 与 subTurns[subtaskId]） */
const subagentGroups = computed(() => {
  if (!detail.value) return []
  return detail.value.subtasks.map(st => {
    const mine = allCalls.value.filter(c => c.subtaskId === st.subtaskId)
    const top = mine.filter(c => c.durationMs != null)
      .slice()
      .sort((a, b) => (b.durationMs as number) - (a.durationMs as number))
      .slice(0, 5)
    const rep = repeatGroups(mine)
    const turns = (detail.value?.subTurns?.[st.subtaskId] ?? [])
      .slice()
      .sort((a, b) => b.durationMs - a.durationMs)
      .slice(0, 5)
    return { ...st, calls: mine, topCalls: top,
             failed: mine.filter(c => c.state === 'error').length,
             repeats: rep, topTurns: turns }
  })
})

function repeatGroups(calls: PerfToolCall[]) {
  const m = new Map<string, { tool: string; argsPreview: string;
                              count: number; totalMs: number }>()
  for (const c of calls) {
    if (c.isDelegation) continue
    const k = `${c.tool}|${c.args}`
    const e = m.get(k) ?? { tool: c.tool, argsPreview: c.args, count: 0,
                            totalMs: 0 }
    e.count += 1
    e.totalMs += c.durationMs ?? 0
    m.set(k, e)
  }
  return [...m.values()].filter(e => e.count >= REPEAT_THRESHOLD)
}

function mdCell(s: string | null | undefined): string {
  return (s || '—').replace(/\|/g, '\\|').replace(/\s+/g, ' ').trim()
}

/** 结构化 Markdown 报告（复制按钮用；页面与文本同源，避免两套口径） */
function buildMarkdown(): string {
  const d = detail.value
  if (!d) return ''
  const L: string[] = []
  L.push('# 子任务性能报告', '')
  L.push(`- 会话：${d.attempt.sessionId}`)
  L.push(`- 执行：${d.attempt.attemptId}（${d.attempt.status}）`)
  L.push(`- 时间：${d.attempt.startedAt ?? '-'} → ${d.attempt.finishedAt ?? '-'}`)
  L.push('', '## 概要', '')
  L.push('| 指标 | 值 |', '| --- | --- |')
  L.push(`| 墙钟 | ${fmtMs(cov.value.wallMs)} |`)
  L.push(`| 模型 | ${fmtMs(cov.value.modelMs)}（${pct(modelRatio.value)}） |`)
  L.push(`| 工具执行 | ${fmtMs(cov.value.toolMs)}（${pct(toolRatio.value)}） |`)
  L.push(`| 子代理等待 | ${fmtMs(cov.value.subagentWaitMs)} |`)
  L.push(`| 空闲 | ${fmtMs(cov.value.idleMs)} |`)
  L.push(`| 轮次 / 调用 / 失败 / 重复组 | ${d.attempt.turns} / `
    + `${callCount.value} / ${failedCalls.value.length} / ${repeats.value.length} |`)
  L.push(`| tokens 输入/输出 | ${d.attempt.tokensIn} / ${d.attempt.tokensOut} |`)

  L.push('', `## 最慢工具调用 Top ${TOP_N}`, '')
  L.push('| # | 工具 | 耗时 | 状态 | 归属 | 参数 | 时间 |',
         '| --- | --- | --- | --- | --- | --- | --- |')
  topCalls.value.forEach((c, i) => L.push(
    `| ${i + 1} | ${c.isDelegation ? '开子代理' : c.tool} | ${fmtMs(c.durationMs)} `
    + `| ${c.state} | ${ownerOf(c)} | ${mdCell(c.args)} | ${c.startedAt ?? '-'} |`))

  L.push('', `## 推理耗时 Top ${TOP_N}`, '')
  L.push('| # | 时间 | 耗时 | tokens 进/出 | 预览 |', '| --- | --- | --- | --- | --- |')
  topTurns.value.forEach((t: PerfTurn, i) => L.push(
    `| ${i + 1} | ${t.createdAt ?? '-'} | ${fmtMs(t.durationMs)} `
    + `| ${t.tokensIn} / ${t.tokensOut} | ${mdCell(t.preview)} |`))

  L.push('', `## 失败工具调用（${failedCalls.value.length}）`, '')
  if (!failedCalls.value.length) {
    L.push('无失败调用')
  } else {
    L.push('| 工具 | 耗时 | 归属 | 参数 | 时间 |', '| --- | --- | --- | --- | --- |')
    failedCalls.value.forEach(c => L.push(
      `| ${c.isDelegation ? '开子代理' : c.tool} | ${fmtMs(c.durationMs)} `
      + `| ${ownerOf(c)} | ${mdCell(c.args)} | ${c.startedAt ?? '-'} |`))
  }

  L.push('', `## 重复调用（同工具+同参数 ≥${REPEAT_THRESHOLD} 次）`, '')
  if (!repeats.value.length) {
    L.push('无重复调用')
  } else {
    L.push('| 工具 | 参数 | 次数 | 累计耗时 |', '| --- | --- | --- | --- |')
    repeats.value.forEach(r => L.push(
      `| ${r.tool} | ${mdCell(r.argsPreview)} | ×${r.count} `
      + `| ${r.totalMs == null ? '-' : fmtMs(r.totalMs)} |`))
  }

  L.push('', `## 子代理分解（${subagentGroups.value.length}）`, '')
  if (!subagentGroups.value.length) {
    L.push('本次执行没有子代理委派')
  } else {
    for (const g of subagentGroups.value) {
      L.push(`### ${g.agent || '(未命名)'}`, '')
      L.push(`- 墙钟 ${fmtMs(g.wallMs)} · 工具 ${g.calls.length} 次`
        + ` · 失败 ${g.failed} · 重复 ${g.repeats.length}`)
      if (g.description) L.push(`- 委派：${mdCell(g.description)}`)
      for (const c of g.topCalls) {
        L.push(`- 最慢调用：${c.tool} ${fmtMs(c.durationMs)}`
          + `（${mdCell(c.args)}）`)
      }
      for (const t of g.topTurns) {
        L.push(`- 推理：${fmtMs(t.durationMs)}（${t.createdAt ?? '-'}）`)
      }
      L.push('')
    }
  }
  return L.join('\n')
}

async function copyMarkdown() {
  const text = buildMarkdown()
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制 Markdown 报告')
  } catch {
    ElMessage.warning('复制失败（浏览器剪贴板受限），可稍后重试')
  }
}

async function load() {
  loading.value = true
  error.value = ''
  empty.value = false
  try {
    if (props.attemptId) {
      detail.value = await perfAttempt(props.attemptId)
    } else if (props.batchId && props.sid) {
      const r = await getAdminChildPerf(props.batchId, props.sid)
      if ('empty' in r && r.empty) {
        empty.value = true
        detail.value = null
      } else {
        detail.value = r as PerfAttemptDetail
      }
    } else {
      error.value = '缺少 batchId/sid 或 attemptId'
    }
  } catch (e) {
    const err = e as { response?: { data?: { error?: string } }; message?: string }
    error.value = err?.response?.data?.error || err?.message || '加载性能数据失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.subtask-perf {
  h4 { margin: 18px 0 8px; }
  h5 { margin: 4px 0 6px; font-size: 13px; }
  &__toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  &__cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    gap: 8px;
    .card {
      border: 1px solid var(--el-border-color-lighter);
      border-radius: 6px;
      padding: 8px 10px;
      text-align: center;
      &.is-danger .num { color: var(--el-color-danger); }
      &.is-warn .num { color: var(--el-color-warning); }
      .num { font-size: 18px; font-weight: 600; }
      .lbl { font-size: 12px; color: var(--el-text-color-secondary); }
    }
  }
  &__tokens { margin-top: 8px; font-size: 12px; }
  &__args { font-size: 12px; }
  &__subagents { margin-top: 4px; }
  &__sub-title { display: inline-flex; align-items: center; gap: 10px; }
  &__sub-desc { margin-bottom: 8px; font-size: 12px; }
  &__sub-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    @media (max-width: 1100px) { grid-template-columns: 1fr; }
  }
  .muted { color: var(--el-text-color-secondary); font-size: 12px; }
}
</style>
