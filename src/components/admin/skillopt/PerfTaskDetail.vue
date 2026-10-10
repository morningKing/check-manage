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
          <ElTableColumn prop="createdAt" label="时间" width="170" />
          <ElTableColumn label="耗时" width="100">
            <template #default="{ row }">
              <span :class="{ 'slow-turn': (row.durationMs || 0) >= 30000 }">
                {{ row.durationMs == null ? '缺数据' : fmtMs(row.durationMs) }}</span>
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
</style>
