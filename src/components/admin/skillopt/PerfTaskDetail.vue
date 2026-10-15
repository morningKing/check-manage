<template>
  <ElDrawer :model-value="true" title="任务耗时分解" size="62%" @close="emit('close')">
    <div v-loading="loading">
      <ElAlert v-if="error" type="error" :closable="false" :title="error" />
      <template v-else-if="detail">
        <!-- 覆盖占比条（spec §5.1）；同时挂诊断「定位」的 segment 锚点 -->
        <div class="cov" data-test="cov-bar" data-diag-anchor="segment:idle">
          <div class="cov__seg" data-test="cov-model" :style="segStyle('model')" />
          <div class="cov__seg" data-test="cov-wait" :style="segStyle('wait')" />
          <div class="cov__seg" data-test="cov-tool" :style="segStyle('tool')" />
          <div class="cov__seg" data-test="cov-idle" :style="segStyle('idle')" />
        </div>
        <div class="cov__legend">
          <span><i class="dot dot--model" />模型推理 {{ pct(ratio(cov.modelMs)) }}</span>
          <span><i class="dot dot--wait" />子代理等待 {{ pct(ratio(cov.subagentWaitMs)) }}</span>
          <span v-if="(cov.toolMs ?? 0) > 0"><i class="dot dot--tool" />工具 {{ pct(ratio(cov.toolMs ?? 0)) }}</span>
          <span><i class="dot dot--idle" />引擎间隙 {{ pct(ratio(cov.idleMs)) }}</span>
          <span class="muted">墙钟 {{ fmtMs(cov.wallMs) }}</span>
          <el-button size="small" type="primary" plain class="cov__report-btn"
                     data-test="open-full-report" @click="showFullReport = true">
            完整性能报告</el-button>
        </div>
        <ElAlert v-if="reconcileGap && !reconciled" type="warning"
                 :closable="false" data-test="reconcile-hint" class="reconcile-hint">
          <template #title>
            本任务{{ reconcileGap }}（旧版本采集/落账缺口的存量数据）。
            <ElButton size="small" type="primary" link data-test="reconcile-btn"
                      :loading="reconciling" @click="onReconcile">从运行时对账补齐</ElButton>
          </template>
        </ElAlert>
        <div class="muted cov__note" data-test="coverage-note">
          口径：四段互斥、之和 = 墙钟。下方表格是原始跨度（模型轮次含轮内的工具与
          子代理等待，子代理彼此并行时也互相重叠），直接相加会大于墙钟——对账以
          本条为准。
        </div>

        <div ref="waterfallEl" class="waterfall" data-test="waterfall" />

        <h4 data-diag-anchor="segment:tools">子代理与工具</h4>
        <ElTable :data="detail.subtasks" size="small" data-test="subtask-table"
                 :show-summary="detail.subtasks.length > 1"
                 :summary-method="subtaskSummary">
          <ElTableColumn prop="agent" label="Agent" width="140" />
          <ElTableColumn prop="description" label="委派" min-width="200" show-overflow-tooltip />
          <ElTableColumn prop="status" label="状态" width="100" />
          <ElTableColumn label="墙钟" width="100">
            <template #default="{ row }">{{ fmtMs(row.wallMs) }}</template>
          </ElTableColumn>
        </ElTable>
        <div v-if="detail.subtasks.length > 1" class="muted">
          合计 {{ fmtMs(subtaskWallSum) }} 为各子代理跨度直接相加——并行委托时会互相
          重叠，对账以覆盖占比条的「子代理等待」为准。
        </div>

        <h4 data-diag-anchor="segment:turns">模型轮次</h4>
        <ElTable :data="detail.turns" size="small" data-test="turn-table"
                 :row-class-name="turnRowClass">
          <ElTableColumn type="expand">
            <template #default="{ row }">
              <div class="turn-calls" data-test="turn-calls">
                <!-- 时间轴分支：推理/工具/委托段按时序铺开（和=轮时长）；
                     task 段的子代理内部分解对齐在同轴上行 -->
                <template v-if="timelineOf(row).segments.length">
                  <TurnTimelineChart :timeline="timelineOf(row)" />
                  <div class="seq-list" data-test="turn-seq-list">
                    <template v-for="(s, i) in timelineOf(row).segments" :key="i">
                      <div class="seq-row" :class="`seq-row--${s.type}`">
                        <ElTag size="small"
                               :type="s.type === 'inference' ? 'warning'
                                 : s.type === 'task' ? 'warning' : 'success'">
                          {{ s.type === 'inference' ? '推理'
                            : (seqToolLabel(s.ref)
                               || (s.type === 'task' ? '开子代理' : '工具')) }}
                        </ElTag>
                        <code v-if="s.type !== 'inference'" class="seq-args">
                          {{ seqArgs(s.ref) || s.label }}</code>
                        <span v-else class="seq-args seq-args--muted">{{ s.label }}</span>
                        <span class="seq-dur"
                              :class="{ 'slow-turn': s.type !== 'inference' && (s.end - s.start) >= 30000 }">
                          {{ fmtMs(s.end - s.start) }}</span>
                      </div>
                      <div v-for="(ns, j) in timelineOf(row).nested[s.ref || ''] || []"
                           :key="`${i}-${j}`" class="seq-row seq-row--nested">
                        <ElTag size="small"
                               :type="ns.type === 'inference' ? 'warning' : 'success'">
                          {{ ns.type === 'inference' ? '推理'
                            : (seqToolLabel(ns.ref) || '工具') }}
                        </ElTag>
                        <code v-if="ns.type !== 'inference'" class="seq-args">
                          {{ seqArgs(ns.ref) || ns.label }}</code>
                        <span v-else class="seq-args seq-args--muted">{{ ns.label }}</span>
                        <span class="seq-dur">{{ fmtMs(ns.end - ns.start) }}</span>
                      </div>
                    </template>
                  </div>
                  <div class="turn-call">
                    <ElTag size="small" type="warning" class="turn-call__tool">模型</ElTag>
                    <code class="turn-call__args">模型推理（毛时长 − 轮内工具与委托）</code>
                    <span class="turn-call__dur" data-test="turn-inference">
                      {{ inferenceLabel(row) }}</span>
                  </div>
                  <div class="muted" v-if="turnSubtotals(row)">
                    {{ turnSubtotals(row) }}
                  </div>
                </template>
                <!-- 回退：无法重建时间轴（旧数据/轮毛缺失）→ 平铺明细 -->
                <template v-else>
                  <div class="turn-call">
                    <ElTag size="small" type="warning" class="turn-call__tool">模型</ElTag>
                    <code class="turn-call__args">模型推理（毛时长 − 轮内工具）</code>
                    <span class="turn-call__dur" data-test="turn-inference">
                      {{ inferenceLabel(row) }}</span>
                  </div>
                  <div v-if="!turnCalls(row).length" class="muted">本轮无工具调用记录。</div>
                  <template v-else>
                    <div v-for="c in turnCalls(row)" :key="c.partId" class="turn-call">
                      <ElTag v-if="c.isDelegation" size="small" type="warning"
                             class="turn-call__tool">开子代理</ElTag>
                      <ElTag v-else size="small"
                             :type="c.state === 'error' ? 'danger' : 'info'"
                             class="turn-call__tool">{{ c.tool }}</ElTag>
                      <code class="turn-call__args">{{ c.args || '（无参数）' }}</code>
                      <span class="turn-call__dur"
                            :class="{ 'slow-turn': (c.durationMs || 0) >= 30000 }">
                        {{ c.durationMs == null ? '-' : fmtMs(c.durationMs) }}</span>
                    </div>
                    <div class="muted" v-if="turnSubtotals(row)">
                      {{ turnSubtotals(row) }}
                    </div>
                  </template>
                </template>
              </div>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="createdAt" label="时间" width="170" />
          <ElTableColumn label="耗时" width="100">
            <template #default="{ row }">
              <span :class="{ 'slow-turn': (row.durationMs || 0) >= 30000 }">
                {{ row.durationMs == null ? '缺数据' : fmtMs(row.durationMs) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn label="推理" width="100">
            <template #default="{ row }">
              <span data-test="turn-inference-cell">{{ inferenceLabel(row) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn label="调用" width="90">
            <template #default="{ row }">
              {{ row.toolCount ? `${row.toolCount} 次` : '-' }}
            </template>
          </ElTableColumn>
          <ElTableColumn prop="tokensIn" label="输入 token" width="110" />
          <ElTableColumn prop="tokensOut" label="输出 token" width="110" />
          <ElTableColumn prop="preview" label="预览" min-width="220" show-overflow-tooltip />
        </ElTable>

        <!-- 工具级耗时区块（spec §5.1/§7）：byTool 聚合表；无时长数据降级为文案 -->
        <h4>工具耗时</h4>
        <ElTable v-if="tools?.durationAvailable && tools.byTool?.length"
                 :data="tools.byTool" size="small" data-test="tool-perf-table">
          <ElTableColumn prop="tool" label="工具" width="160" />
          <ElTableColumn prop="count" label="调用" width="80" />
          <ElTableColumn label="总时长" width="110">
            <template #default="{ row }">{{ row.totalMs == null ? '-' : fmtMs(row.totalMs) }}</template>
          </ElTableColumn>
          <ElTableColumn label="占墙钟" width="100">
            <template #default="{ row }">
              {{ row.totalMs == null ? '-' : pct(cov.wallMs ? row.totalMs / cov.wallMs : 0) }}
            </template>
          </ElTableColumn>
        </ElTable>
        <div v-else class="muted" data-test="tool-perf-placeholder">
          本任务无工具级耗时数据（早于采集上线或无工具调用）。
        </div>
        <template v-if="tools?.calls?.length">
          <div class="muted" style="margin: 8px 0 6px">
            工具调用明细（时间序{{ tools.callsTruncated ? '，仅前 ' + tools.calls.length + ' 条' : '' }}；
            耗时「-」为早于采集上线的旧数据）
          </div>
          <ElTable :data="tools.calls" size="small" data-test="tool-call-list"
                   max-height="360">
            <ElTableColumn type="expand" width="34">
              <template #default="{ row }">
                <div class="call-detail" data-test="tool-call-detail">
                  <div class="call-detail__row">
                    <ElTag size="small" type="info">参数</ElTag>
                    <pre class="call-detail__pre">{{ row.args || '（无参数——该工具不接入参）' }}</pre>
                  </div>
                  <div class="call-detail__row">
                    <ElTag size="small">定位</ElTag>
                    <span class="muted">开始 {{ row.startedAt || '-' }} ·
                      {{ row.subtaskId ? '子代理 ' + row.subtaskId : '根会话' }}</span>
                  </div>
                </div>
              </template>
            </ElTableColumn>
            <ElTableColumn label="时间" width="110">
              <template #default="{ row }">{{ shortTime(row.startedAt) }}</template>
            </ElTableColumn>
            <ElTableColumn label="工具" width="110">
              <template #default="{ row }">
                <ElTag v-if="row.isDelegation" size="small" type="warning">开子代理</ElTag>
                <span v-else>{{ row.tool }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="命令 / 参数" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">
                <code class="call-args">{{ firstArgLine(row.args) || '（无参数）' }}</code>
              </template>
            </ElTableColumn>
            <ElTableColumn label="耗时" width="100">
              <template #default="{ row }">
                <span :class="{ 'slow-turn': (row.durationMs || 0) >= 30000 }">
                  {{ row.durationMs == null ? '-' : fmtMs(row.durationMs) }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="state" label="状态" width="100" />
            <ElTableColumn label="归属" width="110">
              <template #default="{ row }">
                {{ row.subtaskId ? '子代理 ' + row.subtaskId.slice(0, 8) : '根' }}
              </template>
            </ElTableColumn>
          </ElTable>
        </template>

        <!-- Skill 耗时（方案 2 推导）：runtime 精确 / inferred 启发式跨度 -->
        <h4>Skill 耗时</h4>
        <ElTable v-if="skills.length" :data="skills" size="small"
                 data-test="skill-perf-table">
          <ElTableColumn prop="name" label="Skill" min-width="180" show-overflow-tooltip />
          <ElTableColumn label="来源" width="110">
            <template #default="{ row }">
              <ElTag size="small" :type="row.source === 'runtime' ? 'success' : 'info'">
                {{ row.source === 'runtime' ? '精确' : '推导' }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn label="耗时" width="110">
            <template #default="{ row }">
              <span :class="{ 'slow-turn': (row.durationMs || 0) >= 30000 }">
                {{ row.durationMs == null ? '未采集' : fmtMs(row.durationMs) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn label="占墙钟" width="100">
            <template #default="{ row }">
              {{ row.durationMs == null ? '-' : pct(cov.wallMs ? row.durationMs / cov.wallMs : 0) }}
            </template>
          </ElTableColumn>
        </ElTable>
        <div v-else class="muted" data-test="skill-perf-empty">本任务无 skill 调用记录。</div>

        <ElAlert v-if="diagnosesError" type="warning" :closable="false"
                 :title="diagnosesError" />
        <PerfDiagnosisList :diagnoses="diagnoses" />
      </template>
    </div>
  </ElDrawer>
  <!-- 完整性能报告（2026-10-15）：与批任务页共用同一报告组件（attemptId 入口） -->
  <SubtaskPerfReport v-if="showFullReport" :attempt-id="props.attemptId"
                     title="性能报告（含 Top 与失败/重复清单）"
                     @close="showFullReport = false" />
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import {
  perfAttempt, perfAttemptDiagnosis, perfReconcileTaskDurations,
  type PerfAttemptDetail, type Diagnosis,
} from '@/api/aiSkills'
import { useEcharts } from './useEcharts'
import { buildWaterfallOption } from './chartOptions'
import { buildTurnTimeline, type TurnTimeline } from './turnTimeline'
import TurnTimelineChart from './TurnTimelineChart.vue'
import PerfDiagnosisList from './PerfDiagnosisList.vue'
import SubtaskPerfReport from './SubtaskPerfReport.vue'
import { fmtMs, pct } from './format'
import type { PerfToolCall } from '@/api/aiSkills'

const props = defineProps<{ attemptId: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()
/** 完整性能报告抽屉开关（同 attemptId 复用 SubtaskPerfReport） */
const showFullReport = ref(false)

const loading = ref(false)
const error = ref('')
const detail = ref<PerfAttemptDetail | null>(null)
const diagnoses = ref<Diagnosis[]>([])
const diagnosesError = ref('')
const waterfallEl = ref<HTMLElement | null>(null)
const { ready: wfReady, setOption: wfSet } = useEcharts(waterfallEl)

const cov = computed(() => detail.value?.coverage
  ?? { wallMs: 0, modelMs: 0, subagentWaitMs: 0, idleMs: 0, toolMs: 0 })
const tools = computed(() => detail.value?.tools)
const skills = computed(() => detail.value?.skills ?? [])
/** 轮内时间轴（推理/工具/委托交替 + task 委托的子代理嵌套分解） */
function timelineOf(row: { messageId?: string; turnIndex?: number | null }): TurnTimeline {
  const idx = detail.value?.turns.findIndex(t => t.messageId === row.messageId) ?? -1
  const turn = idx >= 0 ? detail.value!.turns[idx] : undefined
  if (!turn) return { segments: [], nested: {}, unplaced: [] }
  return buildTurnTimeline(turn, tools.value?.calls ?? [],
    detail.value?.subTurns, detail.value?.subtasks, idx)
}
/** 子代理墙钟合计（毛和；并行会重叠，对账以覆盖条为准） */
const subtaskWallSum = computed(() =>
  (detail.value?.subtasks ?? []).reduce((acc, s) => acc + (s.wallMs || 0), 0))
function subtaskSummary(): (string | number)[] {
  return ['合计', `${detail.value?.subtasks.length ?? 0} 个子代理（并行会重叠）`,
          '', fmtMs(subtaskWallSum.value)]
}
/** 轮次展开行：该轮内的逐工具调用（turnIndex 归组，时间序）。
 *  子代理自己的调用归 task 段的嵌套分解，不混进本轮根工具列表——
 *  与时间轴根轴同过滤（task 委托行留在根轴，它是父轴的委托事件）。 */
function turnCalls(row: { messageId?: string }): PerfToolCall[] {
  const idx = detail.value?.turns.findIndex(t => t.messageId === row.messageId) ?? -1
  if (idx < 0) return []
  return (tools.value?.calls ?? []).filter(c =>
    c.turnIndex === idx && (!c.subtaskId || c.tool === 'task'))
}
/** 轮内小计：工具（非委派）与「开子代理」委派等待分行——毛时长 = 推理 +
 *  工具 + 委派等待，三类各自可对账。 */function turnSubtotals(row: { messageId?: string }): string {
  const calls = turnCalls(row)
  const parts: string[] = []
  const tools = calls.filter(c => !c.isDelegation)
  const toolWithDur = tools.filter(c => c.durationMs != null)
  if (tools.length) {
    parts.push(`工具 ${tools.length} 次` +
      (toolWithDur.length
        ? ` · 合计 ${fmtMs(toolWithDur.reduce((a, c) => a + (c.durationMs || 0), 0))}` : ''))
  }
  const dels = calls.filter(c => c.isDelegation)
  const delWithDur = dels.filter(c => c.durationMs != null)
  if (dels.length) {
    parts.push(`开子代理 ${dels.length} 次` +
      (delWithDur.length
        ? ` · 等待 ${fmtMs(delWithDur.reduce((a, c) => a + (c.durationMs || 0), 0))}` : ''))
  }
  return parts.join(' ｜ ')
}

/** 轮内模型推理 = 轮毛时长 − 轮内工具时长。轮内存在未采集时长的调用时
 *  只能给出下界（前缀 ≥，其耗时并入推理值）；轮毛时长缺失 → null。 */
function turnInference(row: { messageId?: string }):
        { ms: number | null; complete: boolean } {
  const turn = detail.value?.turns.find(t => t.messageId === row.messageId)
  if (!turn || turn.durationMs == null) return { ms: null, complete: false }
  const calls = turnCalls(row)
  const known = calls.filter(c => c.durationMs != null)
  const toolSum = known.reduce((a, c) => a + (c.durationMs || 0), 0)
  return { ms: Math.max(0, turn.durationMs - toolSum),
           complete: known.length === calls.length }
}

function inferenceLabel(row: { messageId?: string }): string {
  const { ms, complete } = turnInference(row)
  if (ms == null) return '缺数据'
  return (complete ? '' : '≥ ') + fmtMs(ms)
}
/** 参数首行（多行参数在列里只显示第一行，行展开看全文） */
function firstArgLine(args: string): string {
  const i = (args || '').indexOf('\n')
  return i >= 0 ? args.slice(0, i) : args
}
/** 段行标签的工具名：按段 ref=partId 从调用明细取真实工具名（read/write/
 *  bash/grep…），task 委托归「开子代理」。此前段行只标泛称「工具」+参数，
 *  看不出具体是哪个工具（只有明细表有工具列）——2026-10-13 用户报告
 *  「子代理批次中工具耗时只显示了参数和工具」。ref 缺失/未命中回落空串，
 *  由调用方兜底泛称。 */
function seqToolLabel(ref?: string): string {
  if (!ref) return ''
  const c = (tools.value?.calls ?? []).find(x => x.partId === ref)
  if (!c) return ''
  return c.isDelegation ? '开子代理' : c.tool
}

/** 时间轴段行的参数（按段 ref=partId 从调用明细取参数首行；ref 缺失/
 *  未命中返回空串回落段 label）。bash 执行了什么、grep 什么在这里直接可见。
 *  按全量 calls 查而不是本轮 turnCalls——嵌套段（子代理自己的工具）被
 *  轮内列表排除在外，按 turnCalls 查不到会把参数回落成段标签；partId
 *  全局唯一，按全量查同样安全。 */
function seqArgs(ref?: string): string {
  if (!ref) return ''
  const c = (tools.value?.calls ?? []).find(x => x.partId === ref)
  return c ? (firstArgLine(c.args) || '') : ''
}
function shortTime(iso: string | null): string {
  if (!iso) return '-'
  return iso.slice(11, 19)
}
const ratio = (ms: number) => (cov.value.wallMs ? ms / cov.value.wallMs : 0)
const turnRowClass = ({ row }: any) =>
  [(row.durationMs || 0) >= 30_000 ? 'slow-turn-row' : '',
   `diag-anchor-turn-${row.messageId}`].filter(Boolean).join(' ')

function segStyle(kind: 'model' | 'wait' | 'tool' | 'idle') {
  const map = { model: ratio(cov.value.modelMs), wait: ratio(cov.value.subagentWaitMs),
                tool: ratio(cov.value.toolMs ?? 0), idle: ratio(cov.value.idleMs) }
  const color = { model: '#409eff', wait: '#e6a23c', tool: '#67c23a', idle: '#909399' }
  return { width: pct(map[kind]), background: color[kind] }
}

function renderWaterfall() {
  if (wfReady.value && detail.value) wfSet(buildWaterfallOption(detail.value))
}
watch([wfReady, detail], () => nextTick(renderWaterfall), { immediate: true })

/** 需要「从运行时对账补齐」的存量缺口（三种都能由 reconcile 修复）：
 *  ① 开子代理行缺等待时长（时长采集 10-10 之前的旧账）；
 *  ② 有行但缺子会话链接（10-12 前 upsert 不补 subtask_id——嵌套分解画不出）；
 *  ③ 有子代理但一行委派都没有（交互路径 map_part 曾把 task 行整类漏账）。 */
const reconcileGap = computed(() => {
  const calls = detail.value?.tools.calls ?? []
  const dels = calls.filter(c => c.isDelegation)
  const noDur = dels.filter(c => c.durationMs == null).length
  if (noDur) return `有 ${noDur} 条「开子代理」记录缺少等待时长`
  const noLink = dels.filter(c => !c.subtaskId).length
  if (noLink) return `有 ${noLink} 条「开子代理」记录未关联子代理，内部耗时画不出来`
  if (!dels.length && (detail.value?.subtasks.length ?? 0) > 0) {
    return '有子代理但缺少「开子代理」委派记录（时间轴画不出委托段）'
  }
  return ''
})
const reconciling = ref(false)
const reconciled = ref(false)
async function onReconcile() {
  if (reconciling.value) return
  reconciling.value = true
  try {
    const r = await perfReconcileTaskDurations(props.attemptId)
    if (r.status === 'ok') {
      reconciled.value = true
      detail.value = await perfAttempt(props.attemptId)   // 重拉：等待段补齐
    } else {
      reconciled.value = true   // skipped（会话已删/OC 不可达）：不再打扰
    }
  } catch { /* 全局拦截器已提示 */ } finally { reconciling.value = false }
}

onMounted(async () => {
  loading.value = true
  try {
    // 分开请求：分解与诊断互不拖累（诊断挂了不影响主数据渲染）
    detail.value = await perfAttempt(props.attemptId)
    try {
      diagnoses.value = (await perfAttemptDiagnosis(props.attemptId)).diagnoses
    } catch { diagnosesError.value = '诊断加载失败，可稍后重试' }
  } catch (e: any) {
    error.value = e?.message || '加载任务分解失败'
  } finally { loading.value = false }
})
</script>

<style scoped lang="scss">
.cov { display: flex; height: 18px; border-radius: 9px; overflow: hidden; background: var(--el-fill-color); }
.cov__seg { height: 100%; }
.cov__legend { display: flex; gap: 16px; margin: 8px 0 6px; font-size: 13px; align-items: center; flex-wrap: wrap; }
.cov__note { margin: 0 0 16px; font-size: 12px; line-height: 1.6; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
.dot--model { background: #409eff; } .dot--wait { background: #e6a23c; }
.dot--tool { background: #67c23a; } .dot--idle { background: #909399; }
.waterfall { height: 180px; margin-bottom: 16px; }
h4 { margin: 18px 0 8px; }
.slow-turn { color: var(--el-color-danger); font-weight: 600; }
.muted { color: var(--el-text-color-secondary); }
.reconcile-hint { margin-bottom: 10px; }
.turn-calls { padding: 4px 12px; }
.turn-call { display: flex; align-items: center; gap: 8px; padding: 3px 0; }
.turn-call__args { flex: 1; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.turn-call__dur { font-variant-numeric: tabular-nums; font-size: 12px; }
.call-args { font-size: 12px; }
.call-detail { padding: 4px 12px; display: grid; gap: 8px; }
.call-detail__row { display: flex; gap: 8px; align-items: flex-start; }
.call-detail__pre {
  flex: 1; margin: 0; font-size: 12px; white-space: pre-wrap;
  word-break: break-all; max-height: 200px; overflow: auto;
  background: var(--el-fill-color-light); padding: 6px 8px; border-radius: 4px;
}
.seq-list { margin-bottom: 8px; }
.seq-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; }
.seq-row--nested { margin-left: 28px; }
.seq-args { font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.seq-args--muted { color: var(--el-text-color-secondary); }
.seq-dur { font-variant-numeric: tabular-nums; font-size: 12px; }
</style>
