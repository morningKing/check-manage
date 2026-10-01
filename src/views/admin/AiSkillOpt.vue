<template>
  <div class="skillopt">
    <div class="skillopt__header">
      <span class="skillopt__title">SkillOpt — 技能优化</span>
      <ElTag v-if="sourceSummary" size="small" type="info">
        共 {{ sourceSummary.skills }} 个技能 · {{ sourceSummary.invocations }} 次调用
      </ElTag>
      <ElButton size="small" @click="refresh">刷新</ElButton>
    </div>

    <ElTabs v-model="activeTab">
      <!-- ── 任务拟合（主视图）────────────────────────────────────── -->
      <ElTabPane label="任务拟合" name="fit">
        <div v-loading="fitLoading" class="skillopt__pane">
          <ElAlert v-if="fitError" type="error" :closable="false" :title="fitError" />
          <template v-else>
            <h4 class="skillopt__sec">拟合结果</h4>
            <p class="muted">
              定义 frontmatter fit.steps 与执行轨迹的拟合度（点开展开行看步骤明细与偏差诊断）。
            </p>
            <p v-if="!fits.length" class="muted">暂无拟合结果——任务执行后自动计算，也可在展开行手动重算。</p>
            <ElTable v-else :data="fits" size="small" :row-key="fitRowKey"
                     @expand-change="onFitExpand">
              <ElTableColumn type="expand">
                <template #default="{ row }">
                  <p v-if="!detailLoaded(row)" class="muted">加载明细中…</p>
                  <div v-else class="fit-detail">
                    <p v-if="!detailSteps(row).length" class="muted">该定义无步骤明细（无 fit 块或解析失败）。</p>
                    <ElTable v-else :data="detailSteps(row)" size="small">
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

                    <div class="fit-detail__ops">
                      <ElButton size="small" :loading="recomputing === fitRowKey(row)"
                                @click="recomputeFit(row)">重新计算</ElButton>
                      <ElButton v-if="row.status === 'partial' || row.status === 'diverged'"
                                size="small" type="warning"
                                :loading="diagnosing === fitRowKey(row)"
                                @click="diagnose(row)">分析偏差</ElButton>
                    </div>

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
                      <template v-if="diagnosisOf(row)!.revised_steps && diagnosisOf(row)!.revised_steps.length">
                        <div class="fit-diag__revhead">
                          修订步骤草案
                          <ElButton link size="small" @click="copyRevised(diagnosisOf(row)!)">
                            复制 JSON
                          </ElButton>
                        </div>
                        <pre class="fit-diag__json">{{ JSON.stringify(diagnosisOf(row)!.revised_steps, null, 2) }}</pre>
                      </template>
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
              <ElTableColumn label="来源" width="80">
                <template #default="{ row }">
                  <ElTag size="small" :type="row.defKind === 'skill' ? 'success' : 'primary'">
                    {{ kindText(row.defKind) }}
                  </ElTag>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="defName" label="定义" min-width="160" />
              <ElTableColumn label="拟合" min-width="200">
                <template #default="{ row }">
                  <SkillFitBadge :steps-total="row.stepsTotal" :per-step="dotsOf(row)"
                                 :score="row.score" :status="row.status" />
                </template>
              </ElTableColumn>
            </ElTable>

            <h4 class="skillopt__sec">定义版本效果对比</h4>
            <p class="muted">
              按 (来源/定义/内容 hash) 聚合拟合指标的时间线；卡内为相邻版本的平均分变化（↑绿 ↓红）。
            </p>
            <p v-if="!versionGroups.length" class="muted">暂无定义版本数据。</p>
            <div v-for="g in versionGroups" :key="g.key" class="fit-vg">
              <div class="fit-vg__head">
                <ElTag size="small" :type="g.defKind === 'skill' ? 'success' : 'primary'">
                  {{ kindText(g.defKind) }}
                </ElTag>
                <strong>{{ g.defName }}</strong>
              </div>
              <div v-if="deltaCards(g).length" class="fit-deltas">
                <span v-for="(d, i) in deltaCards(g)" :key="i" class="fit-delta"
                      :class="deltaNumClass(d.delta)">
                  {{ d.from }} → {{ d.to }}：
                  <b>{{ fmtDeltaNum(d.delta) }}</b>
                </span>
              </div>
              <ElTable :data="g.versions" size="small">
                <ElTableColumn label="版本 hash" width="130">
                  <template #default="{ row: v }">
                    <span class="mono" :title="v.contentHash">{{ shortHash(v.contentHash) }}</span>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="版本标注" min-width="170">
                  <template #default="{ row: v }">
                    <ElInput v-model="labelDrafts[v.id]" size="small" placeholder="标签，回车保存"
                             @change="saveLabel(v)" />
                  </template>
                </ElTableColumn>
                <ElTableColumn label="首见" width="150">
                  <template #default="{ row: v }">{{ fmtTime(v.firstSeenAt) }}</template>
                </ElTableColumn>
                <ElTableColumn prop="tasks" label="任务" width="70" />
                <ElTableColumn label="平均分" width="80">
                  <template #default="{ row: v }">{{ v.avgScore ?? '—' }}</template>
                </ElTableColumn>
                <ElTableColumn label="拟合率" width="90">
                  <template #default="{ row: v }">{{ fmtRate(v.fitRate) }}</template>
                </ElTableColumn>
                <ElTableColumn label="操作" width="100">
                  <template #default="{ row: v }">
                    <ElButton link size="small" type="primary" @click="openGenerator(v)">
                      生成步骤
                    </ElButton>
                  </template>
                </ElTableColumn>
              </ElTable>
            </div>
          </template>
        </div>
      </ElTabPane>

      <!-- ── 调用聚合（既有视图）──────────────────────────────────── -->
      <ElTabPane label="调用聚合" name="invocations">
        <p class="skillopt__desc">
          基于 ai_skill_invocations 的调用聚合（runtime 插件事件为 confirmed，启发式为
          inferred）。选择技能查看版本对比与应用后的效果追踪。
        </p>
        <div v-loading="loading">
          <ElAlert v-if="error" type="error" :closable="false" :title="error" />
          <template v-else>
            <h4 class="skillopt__sec">技能调用聚合</h4>
            <ElTable :data="skills" size="small" highlight-current-row
                     @current-change="onSelectSkill">
              <ElTableColumn prop="name" label="技能" min-width="180" />
              <ElTableColumn label="版本 hash" width="120">
                <template #default="{ row }">
                  <span class="mono">{{ shortHash(row.skill_hash) }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="invocations" label="调用" width="80" />
              <ElTableColumn label="完成率" width="90">
                <template #default="{ row }">{{ fmtRate(row.completion_rate) }}</template>
              </ElTableColumn>
              <ElTableColumn label="失败" width="70">
                <template #default="{ row }">{{ row.failed }}</template>
              </ElTableColumn>
              <ElTableColumn label="证据" width="120">
                <template #default="{ row }">
                  <ElTag v-if="row.runtime_confirmed" size="small" type="success">运行时确认</ElTag>
                  <ElTag v-else size="small" type="info">启发推断</ElTag>
                </template>
              </ElTableColumn>
            </ElTable>

            <template v-if="selectedSkill">
              <h4 class="skillopt__sec">版本对比 — {{ selectedSkill.name }}</h4>
              <p v-if="!versionsFor(selectedSkill.name).length" class="muted">该技能只有一个版本或无数据。</p>
              <ElTable v-else :data="versionsFor(selectedSkill.name)" size="small">
                <ElTableColumn label="版本 hash" width="120">
                  <template #default="{ row }">
                    <span class="mono">{{ shortHash(row.skill_hash) }}</span>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="invocations" label="调用" width="80" />
                <ElTableColumn label="完成率" width="90">
                  <template #default="{ row }">{{ fmtRate(row.completion_rate) }}</template>
                </ElTableColumn>
                <ElTableColumn label="Δ完成率" width="100">
                  <template #default="{ row }">
                    <span :class="deltaClass(row.completion_rate_delta)">
                      {{ fmtDelta(row.completion_rate_delta) }}
                    </span>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="证据" width="110">
                  <template #default="{ row }">
                    {{ row.runtime_confirmed ? '运行时确认' : '启发推断' }}
                  </template>
                </ElTableColumn>
              </ElTable>
            </template>

            <h4 class="skillopt__sec">建议效果追踪</h4>
            <p class="muted">应用建议后系统自动跟踪前后 7 天窗口的调用成功率。</p>
            <ElTable :data="feedbacks" size="small">
              <ElTableColumn prop="suggestion_id" label="建议" min-width="180">
                <template #default="{ row }">
                  <span class="mono">{{ shortHash(row.suggestion_id) }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="action" label="动作" width="110" />
              <ElTableColumn label="应用时间" width="170">
                <template #default="{ row }">{{ fmtTime(row.applied_at) }}</template>
              </ElTableColumn>
              <ElTableColumn label="效果（7 天窗口完成率）" min-width="200">
                <template #default="{ row }">
                  <span v-if="!row.applied_at" class="muted">—</span>
                  <span v-else-if="effect[row.suggestion_id]">
                    {{ fmtRate(effect[row.suggestion_id].before) }} →
                    {{ fmtRate(effect[row.suggestion_id].after) }}
                  </span>
                  <ElButton v-else link size="small" @click="loadEffect(row.suggestion_id)">
                    查看效果
                  </ElButton>
                </template>
              </ElTableColumn>
            </ElTable>
            <p v-if="!feedbacks.length" class="muted">暂无建议反馈记录。</p>
          </template>
        </div>
      </ElTabPane>
    </ElTabs>

    <!-- ── 步骤生成器对话框 ─────────────────────────────────────────── -->
    <ElDialog v-model="gen.visible" :title="`生成步骤 — ${gen.name || '定义'}`" width="780px"
              append-to-body>
      <div class="gen">
        <div class="gen__row">
          <span class="gen__label">定义路径</span>
          <ElInput v-model="gen.path" size="small"
                   placeholder="定义文件绝对路径（服务端读取并解析 fit.steps）" />
          <ElButton size="small" type="primary" :loading="gen.busy" @click="runGenerate">
            生成步骤
          </ElButton>
        </div>
        <p v-if="gen.error" class="gen__err">{{ gen.error }}</p>

        <template v-if="gen.steps.length">
          <ElTable :data="gen.steps" size="small" class="gen__steps">
            <ElTableColumn label="id" width="150">
              <template #default="{ row }"><ElInput v-model="row.id" size="small" /></template>
            </ElTableColumn>
            <ElTableColumn label="名称" min-width="130">
              <template #default="{ row }"><ElInput v-model="row.name" size="small" /></template>
            </ElTableColumn>
            <ElTableColumn label="tool" width="110">
              <template #default="{ row }"><ElInput v-model="row.tool" size="small" /></template>
            </ElTableColumn>
            <ElTableColumn label="args_pattern" min-width="200">
              <template #default="{ row }">
                <ElInput v-model="row.args_pattern" size="small" />
              </template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="70">
              <template #default="{ $index }">
                <ElButton link size="small" type="danger" @click="removeStep($index)">删除</ElButton>
              </template>
            </ElTableColumn>
          </ElTable>
          <ElButton size="small" @click="addStep">添加步骤</ElButton>

          <div class="gen__preview">
            <div class="gen__row">
              <span class="gen__label">试算 attempt</span>
              <ElInput v-model="gen.attemptId" size="small"
                       placeholder="历史 attemptId（可从上方拟合列表复制）" />
              <ElButton size="small" :loading="gen.previewing" @click="runPreview">试算</ElButton>
            </div>
            <div v-if="gen.preview" class="gen__preview-result">
              <SkillFitBadge :steps-total="gen.preview.steps_total"
                             :per-step="gen.preview.per_step"
                             :score="gen.preview.score" :status="gen.preview.status" />
              <ElTable :data="gen.preview.per_step" size="small" class="gen__ps">
                <ElTableColumn prop="name" label="步骤" min-width="140" />
                <ElTableColumn label="状态" width="90">
                  <template #default="{ row: ps }">
                    <span class="fit-dot" :class="dotClass(ps.status)" />
                    {{ stepStatusText(ps.status) }}
                  </template>
                </ElTableColumn>
                <ElTableColumn label="证据" min-width="220">
                  <template #default="{ row: ps }">
                    <span v-if="ps.evidence && ps.evidence.length" class="mono"
                          :title="ps.evidence[0].args">
                      {{ ps.evidence[0].tool }} · {{ trunc(ps.evidence[0].args, 72) }}
                    </span>
                    <span v-else class="muted">—</span>
                  </template>
                </ElTableColumn>
              </ElTable>
            </div>
          </div>
        </template>
      </div>

      <template #footer>
        <ElButton size="small" @click="gen.visible = false">取消</ElButton>
        <ElButton size="small" type="primary" :loading="gen.saving"
                  :disabled="!gen.steps.length" @click="saveSteps">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  ElTable, ElTableColumn, ElTag, ElAlert, ElButton,
  ElTabs, ElTabPane, ElDialog, ElInput, ElMessage,
} from 'element-plus'
import { get } from '@/utils/request'
import SkillFitBadge from '@/components/admin/SkillFitBadge.vue'
import {
  listSkillFits, getSkillFitDetail, recomputeSkillFit, listSkillDefVersions,
  updateSkillDefVersion, generateSkillDefSteps, applySkillDefSteps,
  previewSkillDefSteps, diagnoseSkillFit,
} from '@/api/aiSkills'
import type {
  SkillFitRow, SkillFitDetail, SkillFitDiagnosis, FitPerStep, FitStep,
  FitPreview, SkillDefVersion,
} from '@/api/aiSkills'

// ── 调用聚合 tab（既有逻辑）────────────────────────────────────────────

interface SkillRow {
  name: string
  skill_hash: string
  invocations: number
  completed: number
  failed: number
  runtime_confirmed: number
  heuristic: number
  completion_rate: number | null
  completion_rate_delta?: number | null
}

const activeTab = ref('fit')
const loading = ref(false)
const error = ref('')
const skills = ref<SkillRow[]>([])
const versions = ref<Array<{ skill: string; versions: SkillRow[] }>>([])
const feedbacks = ref<Array<Record<string, any>>>([])
const effect = ref<Record<string, { before: number | null; after: number | null }>>({})
const selectedSkill = ref<SkillRow | null>(null)
const sourceSummary = computed(() => {
  const inv = skills.value.reduce((s, r) => s + (r.invocations || 0), 0)
  return { skills: skills.value.length, invocations: inv }
})

function versionsFor(skill: string) {
  return versions.value.find(v => v.skill === skill)?.versions || []
}
function onSelectSkill(row: SkillRow | null) {
  selectedSkill.value = row
  if (row) loadVersions(row.name)
}
async function loadVersions(skill: string) {
  try {
    const res = await get<{ versions: Array<{ skill: string; versions: SkillRow[] }> }>(
      '/ai/chat/admin/skill-analytics/versions')
    versions.value = (res.versions || []).filter(v => v.skill === skill)
  } catch { /* 非关键 */ }
}
async function loadEffect(suggestionId: string) {
  try {
    const res = await get<{ status: string; before?: { completionRate: number | null };
      after?: { completionRate: number | null } }>(
      `/ai/chat/admin/skill-suggestions/${suggestionId}/effect`)
    if (res.status === 'tracked') {
      effect.value[suggestionId] = {
        before: res.before?.completionRate ?? null,
        after: res.after?.completionRate ?? null,
      }
    }
  } catch { /* ignore */ }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await get<{ skills: SkillRow[] }>('/ai/chat/admin/skill-analytics')
    skills.value = res.skills || []
    const vf = await get<{ versions: Array<{ skill: string; versions: SkillRow[] }> }>(
      '/ai/chat/admin/skill-analytics/versions').catch(() => ({ versions: [] as any[] }))
    versions.value = vf.versions || []
    const fb = await get<{ feedbacks: Array<Record<string, any>> }>(
      '/ai/chat/admin/suggestion-feedbacks').catch(() => ({ feedbacks: [] as any[] }))
    feedbacks.value = fb.feedbacks || []
  } catch (e: unknown) {
    error.value = errMsg(e)
  } finally {
    loading.value = false
  }
}

// ── 任务拟合：列表 / 明细 / 重算 / 诊断 ────────────────────────────────

const fitLoading = ref(false)
const fitError = ref('')
const fits = ref<SkillFitRow[]>([])
const detailMap = ref<Record<string, { fits: SkillFitDetail[]; loading: boolean; loaded: boolean }>>({})
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

async function ensureFitDetail(attemptId: string) {
  const cur = detailMap.value[attemptId]
  if (cur?.loaded || cur?.loading) return
  detailMap.value[attemptId] = { fits: [], loading: true, loaded: false }
  try {
    const res = await getSkillFitDetail(attemptId)
    detailMap.value[attemptId] = { fits: res.fits || [], loading: false, loaded: true }
  } catch {
    detailMap.value[attemptId] = { fits: [], loading: false, loaded: true }
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
    detailMap.value[row.attemptId] = { fits: res.fits || [], loading: false, loaded: true }
    // 重算覆盖后旧诊断一并失效（后端也会置空 diagnosis 列）
    for (const k of Object.keys(diagMap.value)) {
      if (k.startsWith(`${row.attemptId}|`)) delete diagMap.value[k]
    }
    await loadFits(false)
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

// ── 定义版本时间线 ─────────────────────────────────────────────────────

interface VersionView extends SkillDefVersion {
  deltaScore: number | null
  deltaFitRate: number | null
}
interface VersionGroup {
  key: string
  defKind: string
  defName: string
  versions: VersionView[]
}
const defVersions = ref<SkillDefVersion[]>([])
const labelDrafts = ref<Record<string, string>>({})

const versionGroups = computed<VersionGroup[]>(() => {
  const map = new Map<string, VersionView[]>()
  for (const v of defVersions.value) {
    const key = `${v.defKind}/${v.defName}`
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push({ ...v, deltaScore: null, deltaFitRate: null })
  }
  const groups: VersionGroup[] = []
  for (const [key, list] of map) {
    // 后端 ORDER BY def_name, first_seen_at：组内旧 → 新
    for (let i = 1; i < list.length; i++) {
      const prev = list[i - 1]
      const cur = list[i]
      cur.deltaScore = prev.avgScore != null && cur.avgScore != null
        ? Math.round((cur.avgScore - prev.avgScore) * 10) / 10 : null
      cur.deltaFitRate = prev.fitRate != null && cur.fitRate != null
        ? cur.fitRate - prev.fitRate : null
    }
    const first = list[0]
    groups.push({ key, defKind: first.defKind, defName: first.defName, versions: list })
  }
  return groups
})

function deltaCards(g: VersionGroup) {
  const out: Array<{ from: string; to: string; delta: number | null }> = []
  for (let i = 1; i < g.versions.length; i++) {
    const prev = g.versions[i - 1]
    const cur = g.versions[i]
    out.push({
      from: prev.versionLabel || shortHash(prev.contentHash),
      to: cur.versionLabel || shortHash(cur.contentHash),
      delta: cur.deltaScore,
    })
  }
  return out
}

async function saveLabel(v: SkillDefVersion) {
  const draft = (labelDrafts.value[v.id] ?? '').trim()
  if (draft === (v.versionLabel ?? '')) return
  try {
    await updateSkillDefVersion(v.id, { versionLabel: draft || null })
    ElMessage.success('版本标注已保存')
    await loadDefVersions()
  } catch { /* 全局 toast 已提示 */ }
}

// ── 步骤生成器对话框 ───────────────────────────────────────────────────

interface GenStepRow {
  id: string
  name: string
  tool: string
  args_pattern: string
  /** 生成结果里首个 expect 之外的项，编辑时保留不丢 */
  extraExpect: FitStep['expect']
}
const gen = ref({
  visible: false, kind: '', name: '', path: '',
  steps: [] as GenStepRow[], attemptId: '',
  preview: null as FitPreview | null,
  busy: false, previewing: false, saving: false, error: '',
})

function openGenerator(v: SkillDefVersion) {
  gen.value = {
    visible: true, kind: v.defKind, name: v.defName, path: '',
    steps: [], attemptId: '', preview: null,
    busy: false, previewing: false, saving: false, error: '',
  }
}
function toGenRow(s: FitStep): GenStepRow {
  const expect = s.expect || []
  const [first, ...rest] = expect
  return {
    id: s.id || '', name: s.name || '',
    tool: first?.tool || '', args_pattern: first?.args_pattern || '',
    extraExpect: rest,
  }
}
function fromGenRow(r: GenStepRow): FitStep {
  const expect: FitStep['expect'] = []
  if (r.tool.trim()) {
    expect.push({
      tool: r.tool.trim(),
      ...(r.args_pattern.trim() ? { args_pattern: r.args_pattern.trim() } : {}),
    })
  }
  expect.push(...r.extraExpect)
  return { id: r.id.trim(), name: r.name.trim() || r.id.trim(), expect }
}
function addStep() {
  gen.value.steps.push({ id: '', name: '', tool: '', args_pattern: '', extraExpect: [] })
}
function removeStep(i: number) {
  gen.value.steps.splice(i, 1)
}
async function runGenerate() {
  if (!gen.value.path.trim()) {
    gen.value.error = '请填写定义文件路径'
    return
  }
  gen.value.error = ''
  gen.value.busy = true
  try {
    const res = await generateSkillDefSteps({
      kind: gen.value.kind || undefined, path: gen.value.path.trim(),
    })
    gen.value.steps = (res.steps || []).map(toGenRow)
    gen.value.preview = null
  } catch { /* 全局 toast 已提示 */ } finally {
    gen.value.busy = false
  }
}
async function runPreview() {
  if (!gen.value.attemptId.trim()) {
    gen.value.error = '请填写用于试算的历史 attemptId'
    return
  }
  gen.value.error = ''
  gen.value.previewing = true
  try {
    const res = await previewSkillDefSteps({
      steps: gen.value.steps.map(fromGenRow), attemptId: gen.value.attemptId.trim(),
    })
    gen.value.preview = res.preview
  } catch { /* 全局 toast 已提示 */ } finally {
    gen.value.previewing = false
  }
}
async function saveSteps() {
  if (!gen.value.path.trim()) {
    gen.value.error = '请填写定义文件路径'
    return
  }
  gen.value.error = ''
  gen.value.saving = true
  try {
    const res = await applySkillDefSteps({
      path: gen.value.path.trim(), steps: gen.value.steps.map(fromGenRow),
    })
    ElMessage.success(`已回写 ${res.path}`)
    gen.value.visible = false
  } catch { /* 全局 toast 已提示 */ } finally {
    gen.value.saving = false
  }
}

// ── 数据加载与格式化 ───────────────────────────────────────────────────

async function loadFits(withVersions = true) {
  fitLoading.value = true
  fitError.value = ''
  try {
    const res = await listSkillFits({ limit: 50 })
    fits.value = res.fits || []
  } catch (e: unknown) {
    fitError.value = errMsg(e)
  } finally {
    fitLoading.value = false
  }
  if (withVersions) await loadDefVersions()
}
async function loadDefVersions() {
  try {
    const res = await listSkillDefVersions()
    defVersions.value = res.versions || []
    for (const v of defVersions.value) {
      labelDrafts.value[v.id] = v.versionLabel ?? ''
    }
  } catch { /* 非关键 */ }
}

function refresh() {
  void load()
  void loadFits()
}
onMounted(refresh)

// ── 格式化 helpers ─────────────────────────────────────────────────────

function errMsg(e: unknown) {
  const ax = e as { response?: { data?: { error?: string } }; message?: string }
  return ax?.response?.data?.error || ax?.message || '加载失败'
}
function fmtRate(v: number | null | undefined) {
  return v == null ? '—' : Math.round(v * 100) + '%'
}
function fmtDelta(v: number | null | undefined) {
  if (v == null) return '—'
  return (v > 0 ? '+' : '') + Math.round(v * 100) + '%'
}
function deltaClass(v: number | null | undefined) {
  if (v == null || v === 0) return ''
  return v > 0 ? 'delta-up' : 'delta-down'
}
function fmtDeltaNum(v: number | null | undefined) {
  if (v == null) return '—'
  if (v === 0) return '0'
  return (v > 0 ? '↑ +' : '↓ ') + Math.round(Math.abs(v) * 10) / 10
}
function deltaNumClass(v: number | null | undefined) {
  if (v == null || v === 0) return ''
  return v > 0 ? 'delta-up' : 'delta-down'
}
function shortHash(h?: string | null) {
  return h ? h.slice(0, 10) + '…' : '—'
}
function fmtTime(v?: string | null) {
  return v ? new Date(v).toLocaleString() : '—'
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
function kindText(kind: string) {
  if (kind === 'skill') return '技能'
  if (kind === 'agent') return '代理'
  return kind
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
.skillopt__header { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.skillopt__title { font-size: 16px; font-weight: 700; color: #1a237e; }
.skillopt__desc { color: var(--el-text-color-secondary); font-size: 12.5px; margin: 4px 0 14px; }
.skillopt__sec { font-size: 14px; margin: 18px 0 8px; }
.skillopt__pane { min-height: 120px; }
.mono { font-family: monospace; font-size: 12px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.delta-up { color: var(--el-color-success); font-weight: 600; }
.delta-down { color: var(--el-color-danger); font-weight: 600; }

.fit-detail { padding: 4px 12px 12px 48px; }
.fit-detail__ops { display: flex; gap: 8px; margin: 10px 0 4px; }
.fit-diag {
  margin-top: 8px; padding: 10px 12px; border-radius: 6px;
  background: var(--el-fill-color-light); border: 1px solid var(--el-border-color-lighter);
}
.fit-diag__cause { font-size: 13px; margin-bottom: 6px; }
.fit-diag__sugs { margin: 4px 0 8px; padding-left: 20px; font-size: 12.5px; }
.fit-diag__sugs li { margin: 2px 0; }
.fit-diag__revhead { display: flex; align-items: center; gap: 8px; font-size: 12.5px; margin: 6px 0 4px; }
.fit-diag__json {
  margin: 0; padding: 8px 10px; font-size: 12px; line-height: 1.5;
  background: var(--el-fill-color-darker); border-radius: 4px;
  max-height: 260px; overflow: auto; white-space: pre-wrap; word-break: break-all;
}

.fit-vg { margin-bottom: 18px; }
.fit-vg__head { display: flex; align-items: center; gap: 8px; margin: 8px 0 6px; }
.fit-deltas { display: flex; flex-wrap: wrap; gap: 8px; margin: 4px 0 8px; }
.fit-delta {
  font-size: 12px; padding: 2px 10px; border-radius: 10px;
  background: var(--el-fill-color); color: var(--el-text-color-regular);
}
.fit-delta b { font-variant-numeric: tabular-nums; }

.fit-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  margin-right: 4px; vertical-align: middle;
}
.fit-dot.is-hit { background: var(--el-color-success); }
.fit-dot.is-miss { background: var(--el-color-danger); }
.fit-dot.is-skipped { background: var(--el-border-color-darker); opacity: .55; }

.gen__row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.gen__label { flex: none; width: 72px; font-size: 12.5px; color: var(--el-text-color-secondary); }
.gen__row .el-input { flex: 1; }
.gen__err { color: var(--el-color-danger); font-size: 12.5px; margin: 0 0 8px; }
.gen__steps { margin-bottom: 8px; }
.gen__preview { margin-top: 12px; border-top: 1px dashed var(--el-border-color); padding-top: 10px; }
.gen__preview-result { margin-top: 8px; }
.gen__ps { margin-top: 8px; }
</style>
