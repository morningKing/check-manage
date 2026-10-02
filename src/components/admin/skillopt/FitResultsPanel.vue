<template>
  <div class="fit-results">
    <div class="fit-results__bar">
      <ElRadioGroup v-model="statusFilter" size="small">
        <ElRadioButton value="">全部</ElRadioButton>
        <ElRadioButton value="fit">拟合</ElRadioButton>
        <ElRadioButton value="partial">部分</ElRadioButton>
        <ElRadioButton value="diverged">偏离</ElRadioButton>
        <ElRadioButton value="other">其他</ElRadioButton>
      </ElRadioGroup>
    </div>
    <p v-if="!filteredFits.length" class="muted">
      {{ statusFilter ? '当前筛选无结果' : '该定义暂无拟合结果。' }}
    </p>
    <ElTable v-else :data="filteredFits" size="small" :row-key="fitRowKey"
             @expand-change="onFitExpand">
      <ElTableColumn type="expand">
        <template #default="{ row }">
          <div v-if="!detailLoaded(row)" class="muted">加载明细中…</div>
          <div v-else class="fit-detail">
            <div class="fit-detail__ops">
              <ElButton size="small" :loading="recomputing === fitRowKey(row)"
                        @click="recomputeFit(row)">重新计算</ElButton>
              <ElButton v-if="row.status === 'partial' || row.status === 'diverged'"
                        size="small" type="warning"
                        :loading="diagnosing === fitRowKey(row)"
                        @click="diagnose(row)">分析偏差</ElButton>
            </div>
            <div class="fit-detail__grid">
              <div>
                <p v-if="!detailSteps(row).length" class="muted">
                  该定义无步骤明细（无 fit 块或解析失败）。
                </p>
                <template v-else>
                  <ElTable :data="detailSteps(row)" size="small">
                    <ElTableColumn prop="name" label="步骤" min-width="150" />
                    <ElTableColumn label="状态" width="100">
                      <template #default="{ row: ps }">
                        <span class="fit-dot" :class="dotClass(ps.status)" />
                        {{ stepStatusText(ps.status) }}
                      </template>
                    </ElTableColumn>
                    <ElTableColumn label="证据（tool · args）" min-width="260">
                      <template #default="{ row: ps }">
                        <span v-if="ps.evidence && ps.evidence.length" class="mono"
                              :title="ps.evidence[0].args">
                          {{ ps.evidence[0].tool }} · {{ trunc(ps.evidence[0].args, 96) }}
                        </span>
                        <span v-else class="muted">—</span>
                      </template>
                    </ElTableColumn>
                  </ElTable>

                  <template v-if="subagentFitsOf(row).length">
                    <p class="muted" style="margin:8px 0 4px">
                      子代理层拟合（按层独立判定，不并入上方父级）：
                    </p>
                    <ElTable :data="subagentFitsOf(row)" size="small">
                      <ElTableColumn label="子代理" width="120">
                        <template #default="{ row: sf }">{{ sf.subtaskAgent || '—' }}</template>
                      </ElTableColumn>
                      <ElTableColumn prop="defName" label="定义" min-width="140" />
                      <ElTableColumn label="拟合" min-width="180">
                        <template #default="{ row: sf }">
                          <SkillFitBadge :steps-total="sf.stepsTotal"
                                         :per-step="sf.perStep"
                                         :score="sf.score" :status="sf.status" />
                        </template>
                      </ElTableColumn>
                    </ElTable>
                  </template>
                </template>
              </div>
              <div>
                <div v-if="diagnosisOf(row)" class="fit-diag">
                  <div class="fit-diag__cause">
                    偏差原因：
                    <ElTag size="small" :type="causeTagType(diagnosisOf(row)!.cause)">
                      {{ causeLabel(diagnosisOf(row)!.cause) }}
                    </ElTag>
                  </div>
                  <ol v-if="diagnosisOf(row)!.suggestions.length" class="fit-diag__sugs">
                    <li v-for="(s, i) in diagnosisOf(row)!.suggestions" :key="i">{{ s }}</li>
                  </ol>
                  <ElCollapse
                    v-if="diagnosisOf(row)!.revised_steps && diagnosisOf(row)!.revised_steps.length">
                    <ElCollapseItem name="revised">
                      <template #title>
                        <span class="fit-diag__revhead">
                          修订步骤草案
                          <ElButton link size="small"
                                    @click.stop="copyRevised(diagnosisOf(row)!)">
                            复制 JSON
                          </ElButton>
                        </span>
                      </template>
                      <pre class="fit-diag__json">{{
                        JSON.stringify(diagnosisOf(row)!.revised_steps, null, 2)
                      }}</pre>
                    </ElCollapseItem>
                  </ElCollapse>
                </div>
              </div>
            </div>
          </div>
        </template>
      </ElTableColumn>
      <ElTableColumn label="时间" width="165">
        <template #default="{ row }">{{ fmtTime(row.computedAt) }}</template>
      </ElTableColumn>
      <ElTableColumn label="会话" width="120">
        <template #default="{ row }">
          <span class="mono" :title="row.sessionId">{{ shortHash(row.sessionId) }}</span>
        </template>
      </ElTableColumn>
      <ElTableColumn label="Agent" width="140">
        <template #default="{ row }">
          <span v-if="row.agent" class="mono" :title="row.agent">{{ trunc(row.agent, 16) }}</span>
          <span v-else class="muted">—</span>
        </template>
      </ElTableColumn>
      <ElTableColumn label="拟合" min-width="220">
        <template #default="{ row }">
          <SkillFitBadge :steps-total="row.stepsTotal" :per-step="dotsOf(row)"
                         :score="row.score" :status="row.status" />
        </template>
      </ElTableColumn>
    </ElTable>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  ElTable, ElTableColumn, ElButton, ElRadioGroup, ElRadioButton,
  ElTag, ElCollapse, ElCollapseItem, ElMessage,
} from 'element-plus'
import SkillFitBadge from '@/components/admin/SkillFitBadge.vue'
import {
  listSkillFits, getSkillFitDetail, recomputeSkillFit, diagnoseSkillFit,
} from '@/api/aiSkills'
import type {
  SkillFitRow, SkillFitDetail, SkillFitDiagnosis, FitPerStep,
} from '@/api/aiSkills'

const props = defineProps<{ defKind: string; defName: string }>()
const emit = defineEmits<{ (e: 'recomputed'): void }>()

const fits = ref<SkillFitRow[]>([])
const statusFilter = ref('')
/** 「其他」兜底集：不在其中的状态一律归入其他 */
const OTHER_STATUSES = ['failed', 'inconclusive', 'no_trace', 'parse_error']
const filteredFits = computed(() => {
  if (!statusFilter.value) return fits.value
  if (statusFilter.value === 'other')
    return fits.value.filter(f => OTHER_STATUSES.includes(f.status))
  return fits.value.filter(f => f.status === statusFilter.value)
})

const detailMap = ref<Record<string, {
  fits: SkillFitDetail[]; subagentFits: SkillFitDetail[]
  loading: boolean; loaded: boolean
}>>({})
const diagMap = ref<Record<string, SkillFitDiagnosis>>({})
const recomputing = ref('')
const diagnosing = ref('')

function fitRowKey(row: SkillFitRow) {
  return `${row.attemptId}|${row.defKind}|${row.defName}`
}
function detailOf(row: SkillFitRow): SkillFitDetail | null {
  const found = detailMap.value[row.attemptId]?.fits
    ?.find(f => f.defKind === row.defKind && f.defName === row.defName)
  return found ?? null
}
function detailSteps(row: SkillFitRow): FitPerStep[] {
  return detailOf(row)?.perStep ?? []
}
function detailLoaded(row: SkillFitRow): boolean {
  return detailMap.value[row.attemptId]?.loaded ?? false
}
/** 行内拟合点：明细拉到后行徽标也能显示分段点 */
function dotsOf(row: SkillFitRow): FitPerStep[] {
  return detailSteps(row)
}
function diagnosisOf(row: SkillFitRow): SkillFitDiagnosis | null {
  return diagMap.value[fitRowKey(row)] ?? detailOf(row)?.diagnosis ?? null
}
/** 子代理层拟合子条目（spec §10 非目标④转正）：明细端点返回的 subagentFits。 */
function subagentFitsOf(row: SkillFitRow): SkillFitDetail[] {
  return detailMap.value[row.attemptId]?.subagentFits ?? []
}

async function ensureFitDetail(attemptId: string) {
  const cur = detailMap.value[attemptId]
  if (cur?.loaded || cur?.loading) return
  detailMap.value[attemptId] = { fits: [], subagentFits: [], loading: true, loaded: false }
  try {
    const res = await getSkillFitDetail(attemptId)
    detailMap.value[attemptId] = { fits: res.fits || [],
      subagentFits: res.subagentFits ?? [], loading: false, loaded: true }
  } catch {
    detailMap.value[attemptId] = { fits: [], subagentFits: [], loading: false, loaded: true }
  }
}
function onFitExpand(row: SkillFitRow, expanded: SkillFitRow[]) {
  const open = expanded.some(x => fitRowKey(x) === fitRowKey(row))
  if (open) void ensureFitDetail(row.attemptId)
}

async function recomputeFit(row: SkillFitRow) {
  recomputing.value = fitRowKey(row)
  try {
    const res = await recomputeSkillFit(row.attemptId)
    detailMap.value[row.attemptId] = { fits: res.fits || [], subagentFits: [], loading: false, loaded: true }
    // 重算覆盖后旧诊断一并失效（后端也会置空 diagnosis 列）
    for (const k of Object.keys(diagMap.value)) {
      if (k.startsWith(`${row.attemptId}|`)) delete diagMap.value[k]
    }
    await load()
    // 重算成功后通知父组件刷新汇总
    emit('recomputed')
    ElMessage.success('已重新计算')
  } catch { /* 全局 toast 已提示 */ } finally {
    recomputing.value = ''
  }
}

async function diagnose(row: SkillFitRow) {
  if (!row.id) {
    ElMessage.warning('该结果行缺少 id，无法诊断（请刷新列表）')
    return
  }
  diagnosing.value = fitRowKey(row)
  try {
    const res = await diagnoseSkillFit(row.id)
    diagMap.value[fitRowKey(row)] = res.diagnosis
  } catch { /* 全局 toast 已提示 */ } finally {
    diagnosing.value = ''
  }
}

async function copyRevised(diag: SkillFitDiagnosis) {
  try {
    await navigator.clipboard.writeText(JSON.stringify(diag.revised_steps ?? [], null, 2))
    ElMessage.success('修订步骤 JSON 已复制')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}

/** 序列守卫：切定义后慢返回的旧响应不得覆盖当前列表 */
let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  try {
    const res = await listSkillFits({
      defKind: props.defKind, defName: props.defName, limit: 100,
    })
    if (seq !== loadSeq) return
    fits.value = res.fits || []
  } catch { /* 全局 toast 已提示 */ }
}
watch([() => props.defKind, () => props.defName], () => {
  statusFilter.value = ''
  fits.value = []
  detailMap.value = {}
  diagMap.value = {}
  void load().catch(() => { /* 全局 toast 已提示 */ })
}, { immediate: true })

// ── 格式化 helpers（从父组件就近迁入）─────────────────────────────────

function fmtTime(v?: string | null) {
  return v ? new Date(v).toLocaleString() : '—'
}
function shortHash(h?: string | null) {
  return h ? h.slice(0, 10) + '…' : '—'
}
function trunc(s: string | undefined, n: number) {
  const t = s || ''
  return t.length > n ? t.slice(0, n) + '…' : t
}
function dotClass(status: string) {
  if (status === 'hit') return 'is-hit'
  if (status === 'miss') return 'is-miss'
  return 'is-skipped'
}
function stepStatusText(status: string) {
  if (status === 'hit') return '命中'
  if (status === 'miss') return '未命中'
  if (status === 'skipped') return '跳过'
  return status
}

const CAUSE_LABELS: Record<string, string> = {
  definition_stale: '定义过时',
  step_redundant: '步骤冗余',
  order_deviation: '顺序偏差',
  model_noncompliance: '模型未遵循',
  environment: '环境异常',
}
function causeLabel(cause?: string | null) {
  return (cause && CAUSE_LABELS[cause]) || cause || '—'
}
function causeTagType(cause?: string | null): 'warning' | 'danger' | 'info' {
  if (cause === 'model_noncompliance') return 'danger'
  if (cause === 'environment') return 'info'
  return 'warning'
}
</script>

<style scoped>
.fit-results__bar { margin-bottom: 8px; }
.mono { font-family: monospace; font-size: 12px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }

.fit-detail { padding: 4px 12px 12px 48px; }
.fit-detail__ops { display: flex; justify-content: flex-end; gap: 8px; margin: 0 0 8px; }
.fit-detail__grid { display: grid; grid-template-columns: 3fr 2fr; gap: 16px; }

.fit-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  margin-right: 4px; vertical-align: middle;
}
.fit-dot.is-hit { background: var(--el-color-success); }
.fit-dot.is-miss { background: var(--el-color-danger); }
.fit-dot.is-skipped { background: var(--el-border-color-darker); opacity: .55; }

.fit-diag {
  margin-top: 8px; padding: 10px 12px; border-radius: 6px;
  background: var(--el-fill-color-light); border: 1px solid var(--el-border-color-lighter);
}
.fit-diag__cause { font-size: 13px; margin-bottom: 6px; }
.fit-diag__sugs { margin: 4px 0 8px; padding-left: 20px; font-size: 12.5px; }
.fit-diag__sugs li { margin: 2px 0; }
.fit-diag__revhead { display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
.fit-diag__json {
  margin: 0; padding: 8px 10px; font-size: 12px; line-height: 1.5;
  background: var(--el-fill-color-darker); border-radius: 4px;
  max-height: 260px; overflow: auto; white-space: pre-wrap; word-break: break-all;
}
</style>
