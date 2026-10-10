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
          <span><i class="dot dot--model" />模型 {{ pct(ratio(cov.modelMs)) }}</span>
          <span><i class="dot dot--wait" />子代理等待 {{ pct(ratio(cov.subagentWaitMs)) }}</span>
          <span v-if="(cov.toolMs ?? 0) > 0"><i class="dot dot--tool" />工具 {{ pct(ratio(cov.toolMs ?? 0)) }}</span>
          <span><i class="dot dot--idle" />引擎间隙 {{ pct(ratio(cov.idleMs)) }}</span>
          <span class="muted">墙钟 {{ fmtMs(cov.wallMs) }}</span>
        </div>

        <div ref="waterfallEl" class="waterfall" data-test="waterfall" />

        <h4 data-diag-anchor="segment:tools">子代理与工具</h4>
        <ElTable :data="detail.subtasks" size="small" data-test="subtask-table">
          <ElTableColumn prop="agent" label="Agent" width="140" />
          <ElTableColumn prop="description" label="委派" min-width="200" show-overflow-tooltip />
          <ElTableColumn prop="status" label="状态" width="100" />
          <ElTableColumn label="墙钟" width="100">
            <template #default="{ row }">{{ fmtMs(row.wallMs) }}</template>
          </ElTableColumn>
        </ElTable>

        <h4 data-diag-anchor="segment:turns">模型轮次</h4>
        <ElTable :data="detail.turns" size="small" data-test="turn-table"
                 :row-class-name="turnRowClass">
          <ElTableColumn type="expand">
            <template #default="{ row }">
              <div class="turn-calls" data-test="turn-calls">
                <div v-if="!turnCalls(row).length" class="muted">本轮无工具调用记录。</div>
                <div v-for="c in turnCalls(row)" :key="c.partId" class="turn-call">
                  <ElTag size="small" :type="c.state === 'error' ? 'danger' : 'info'"
                         class="turn-call__tool">{{ c.tool }}</ElTag>
                  <code class="turn-call__args">{{ c.args || '（无参数）' }}</code>
                  <span class="turn-call__dur"
                        :class="{ 'slow-turn': (c.durationMs || 0) >= 30000 }">
                    {{ c.durationMs == null ? '-' : fmtMs(c.durationMs) }}</span>
                </div>
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
          <ElTableColumn label="工具" width="90">
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
            <ElTableColumn label="时间" width="110">
              <template #default="{ row }">{{ shortTime(row.startedAt) }}</template>
            </ElTableColumn>
            <ElTableColumn prop="tool" label="工具" width="110" />
            <ElTableColumn label="命令 / 参数" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">
                <code class="call-args">{{ row.args || '（无参数）' }}</code>
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
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import {
  perfAttempt, perfAttemptDiagnosis, type PerfAttemptDetail, type Diagnosis,
} from '@/api/aiSkills'
import { useEcharts } from './useEcharts'
import { buildWaterfallOption } from './chartOptions'
import PerfDiagnosisList from './PerfDiagnosisList.vue'
import { fmtMs, pct } from './format'
import type { PerfToolCall } from '@/api/aiSkills'

const props = defineProps<{ attemptId: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

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
/** 轮次展开行：该轮内的逐工具调用（turnIndex 归组，时间序） */
function turnCalls(row: { messageId?: string }): PerfToolCall[] {
  const idx = detail.value?.turns.findIndex(t => t.messageId === row.messageId) ?? -1
  if (idx < 0) return []
  return (tools.value?.calls ?? []).filter(c => c.turnIndex === idx)
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
.cov__legend { display: flex; gap: 16px; margin: 8px 0 16px; font-size: 13px; align-items: center; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
.dot--model { background: #409eff; } .dot--wait { background: #e6a23c; }
.dot--tool { background: #67c23a; } .dot--idle { background: #909399; }
.waterfall { height: 180px; margin-bottom: 16px; }
h4 { margin: 18px 0 8px; }
.slow-turn { color: var(--el-color-danger); font-weight: 600; }
.muted { color: var(--el-text-color-secondary); }
.turn-calls { padding: 4px 12px; }
.turn-call { display: flex; align-items: center; gap: 8px; padding: 3px 0; }
.turn-call__args { flex: 1; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.turn-call__dur { font-variant-numeric: tabular-nums; font-size: 12px; }
.call-args { font-size: 12px; }
</style>
