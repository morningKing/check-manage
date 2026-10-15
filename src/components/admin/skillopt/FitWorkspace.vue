<template>
  <div v-loading="loading" class="ws">
    <DefOverview :def="def" @configure="openGenerator(latestAttemptId)"
                 @open-issue="openLatestIssue" />

    <section class="ws__sec">
      <div class="ws__sec-head">
        <h3>步骤命中情况</h3>
        <span class="ws__hint">按步骤看哪一步最常缺失（最近 10 次运行）</span>
      </div>
      <StepMatrix :steps="steps" :runs="detailRuns" @open-run="openRun" />
    </section>

    <section class="ws__sec">
      <div class="ws__sec-head">
        <h3>运行记录</h3>
        <span class="ws__hint">最近 {{ runs.length }} 次，点击一行查看步骤证据与诊断</span>
      </div>
      <RunsTable :runs="runs" :missing="missing" @open-run="openRun" />
    </section>

    <ElCollapse class="ws__versions">
      <ElCollapseItem name="versions" :title="`版本历史（${def.versions} 个）`">
        <DefVersionTimeline :def-kind="def.defKind" :def-name="def.defName"
                            @generate="openGeneratorFromVersion" />
      </ElCollapseItem>
    </ElCollapse>

    <RunDrawer v-model:visible="drawerVisible" :row="openRow" :detail="openDetail"
               :subagent-fits="openSubagentFits" :diagnosis="openDiagnosis"
               :recomputing="recomputing" :diagnosing="diagnosing"
               @recompute="recomputeOpen" @diagnose="diagnoseOpen"
               @copy-revised="copyRevised" @configure="openGenerator" />

    <StepGeneratorDialog :visible="genVisible" :def-kind="def.defKind"
                         :def-name="def.defName" :def-hash="genHash"
                         :attempt-id="genAttemptId"
                         @update:visible="genVisible = $event" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElCollapse, ElCollapseItem } from 'element-plus'
import DefOverview from './DefOverview.vue'
import StepMatrix from './StepMatrix.vue'
import RunsTable from './RunsTable.vue'
import RunDrawer from './RunDrawer.vue'
import DefVersionTimeline from './DefVersionTimeline.vue'
import StepGeneratorDialog from './StepGeneratorDialog.vue'
import {
  listSkillFits, getSkillFitDetail, recomputeSkillFit, diagnoseSkillFit,
} from '@/api/aiSkills'
import type {
  SkillFitDefinitionSummary, SkillFitDetail, SkillFitDiagnosis, SkillFitRow,
  SkillDefVersion,
} from '@/api/aiSkills'
import { collectSteps, runMissSummary } from './fitView'
import type { StepColumn } from './fitView'

const props = defineProps<{ def: SkillFitDefinitionSummary }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const RUN_LIMIT = 20

const loading = ref(false)
const runs = ref<SkillFitRow[]>([])
/** attemptId → 该次运行的明细（fit 为本定义的行，subagentFits 为该次运行的子代理层） */
interface DetailEntry { fit: SkillFitDetail | null; subagentFits: SkillFitDetail[] }
const details = ref<Record<string, DetailEntry>>({})
/** attemptId → 诊断结果（手动分析后的，优先于明细里已有的） */
const diagMap = ref<Record<string, SkillFitDiagnosis>>({})

const drawerVisible = ref(false)
const openId = ref<string | null>(null)
const recomputing = ref(false)
const diagnosing = ref(false)

const genVisible = ref(false)
const genAttemptId = ref('')
const genHash = ref('')

/** 带明细的运行列表（无明细的运行以空 perStep 占位，保证矩阵列与运行记录一致） */
const detailRuns = computed<SkillFitDetail[]>(() => runs.value.map(r => {
  const fit = details.value[r.attemptId]?.fit
  return fit ?? { ...r, perStep: [], diagnosis: null }
}))
const steps = computed<StepColumn[]>(() => collectSteps(detailRuns.value))
const missing = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const r of detailRuns.value) {
    const s = runMissSummary(r)
    if (s) out[r.attemptId] = s
  }
  return out
})
const latestAttemptId = computed(() => runs.value[0]?.attemptId ?? '')

const openRow = computed(() => runs.value.find(r => r.attemptId === openId.value) ?? null)
const openDetail = computed(() => openRow.value ? detailRuns.value.find(d => d.attemptId === openId.value) ?? null : null)
const openSubagentFits = computed(() => openId.value ? details.value[openId.value]?.subagentFits ?? [] : [])
const openDiagnosis = computed(() => {
  if (!openId.value) return null
  return diagMap.value[openId.value] ?? openDetail.value?.diagnosis ?? null
})

/** 序列守卫：切换定义后慢返回的旧响应不得覆盖当前视图 */
let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const res = await listSkillFits({
      defKind: props.def.defKind, defName: props.def.defName, limit: RUN_LIMIT,
    })
    const rows = res.fits || []
    const ids = [...new Set(rows.map(r => r.attemptId))]
    const pairs = await Promise.all(ids.map(async (id): Promise<[string, DetailEntry]> => {
      try {
        const r = await getSkillFitDetail(id)
        const fit = (r.fits || []).find(f => f.defKind === props.def.defKind
          && f.defName === props.def.defName) ?? null
        return [id, { fit, subagentFits: r.subagentFits ?? [] }]
      } catch {
        return [id, { fit: null, subagentFits: [] }]
      }
    }))
    if (seq !== loadSeq) return
    runs.value = rows
    details.value = Object.fromEntries(pairs)
  } catch { /* 全局 toast 已提示 */ } finally {
    if (seq === loadSeq) loading.value = false
  }
}

watch(() => [props.def.defKind, props.def.defName], () => {
  runs.value = []
  details.value = {}
  diagMap.value = {}
  drawerVisible.value = false
  openId.value = null
  void load()
}, { immediate: true })

function openRun(attemptId: string) {
  openId.value = attemptId
  drawerVisible.value = true
}
function openLatestIssue() {
  const hit = runs.value.find(r => r.status === 'diverged' || r.status === 'partial')
  if (hit) openRun(hit.attemptId)
}

async function recomputeOpen() {
  const id = openId.value
  if (!id) return
  recomputing.value = true
  try {
    await recomputeSkillFit(id)
    delete diagMap.value[id]
    await load()
    emit('changed')
    ElMessage.success('已重新计算')
  } catch { /* 全局 toast 已提示 */ } finally {
    recomputing.value = false
  }
}

async function diagnoseOpen() {
  const row = openRow.value
  if (!row) return
  if (!row.id) {
    ElMessage.warning('该运行缺少结果 ID，请刷新后重试')
    return
  }
  diagnosing.value = true
  try {
    const res = await diagnoseSkillFit(row.id)
    diagMap.value[row.attemptId] = res.diagnosis
  } catch { /* 全局 toast 已提示 */ } finally {
    diagnosing.value = false
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

function openGenerator(attemptId: string) {
  genAttemptId.value = attemptId || latestAttemptId.value
  genHash.value = ''
  genVisible.value = true
}
function openGeneratorFromVersion(v: SkillDefVersion) {
  genAttemptId.value = latestAttemptId.value
  genHash.value = v.contentHash || ''
  genVisible.value = true
}

// 保存步骤后定义已变化，通知父级刷新汇总
watch(genVisible, (v, old) => {
  if (old && !v) emit('changed')
})
</script>

<style scoped>
.ws { display: flex; flex-direction: column; gap: 18px; min-height: 200px; }
.ws__sec-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.ws__sec-head h3 { margin: 0; font-size: 14px; }
.ws__hint { font-size: 12px; color: var(--el-text-color-secondary); }
.ws__versions { margin-top: -6px; }
</style>
