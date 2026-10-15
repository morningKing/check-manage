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
          <div v-else-if="!definitions.length" class="fit-empty">
            <p class="fit-empty__title">还没有拟合数据</p>
            <ol class="fit-empty__steps">
              <li>在技能或代理定义的 frontmatter 里写 <code>fit.steps</code>，声明应当执行的步骤</li>
              <li>任务执行后系统自动对照实际工具调用轨迹，判断每一步是否执行</li>
              <li>回到这里查看结果，偏离时给出原因与修订建议</li>
            </ol>
          </div>
          <template v-else>
            <div class="fit-summary">
              <span><b>{{ definitions.length }}</b> 个定义</span>
              <span :class="{ 'is-bad': attentionCount > 0 }">
                <b>{{ attentionCount }}</b> 个需要关注
              </span>
              <span><b>{{ totalRuns }}</b> 次累计运行</span>
              <ElButton link size="small" class="fit-summary__top"
                        @click="globalPatternsVisible = true">
                查看跨定义的偏离模式
              </ElButton>
            </div>
            <div class="fit-layout">
              <aside class="fit-layout__side">
                <FitDefinitionList :definitions="definitions" :selected="selected"
                                   @select="selected = $event" />
              </aside>
              <section v-if="selected" class="fit-layout__main">
                <FitWorkspace :key="selected.defKind + '/' + selected.defName"
                              :def="selected" @changed="loadDefinitions" />
              </section>
            </div>
          </template>
        </div>
      </ElTabPane>

      <!-- ── 性能分析（2026-10-09 spec）────────────────────────────── -->
      <ElTabPane label="性能分析" name="perf">
        <PerfView />
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

    <!-- ── 页面级对话框（与两个 tab 平级）───────────────────────────── -->
    <ElDialog v-model="globalPatternsVisible" title="跨定义偏离模式（步骤缺失 Top）"
              width="860px" append-to-body>
      <DefPatternsPanel v-if="globalPatternsVisible" />
    </ElDialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  ElTable, ElTableColumn, ElTag, ElAlert, ElButton,
  ElTabs, ElTabPane, ElDialog,
} from 'element-plus'
import { get } from '@/utils/request'
import FitDefinitionList from '@/components/admin/skillopt/FitDefinitionList.vue'
import FitWorkspace from '@/components/admin/skillopt/FitWorkspace.vue'
import DefPatternsPanel from '@/components/admin/skillopt/DefPatternsPanel.vue'
import PerfView from '@/components/admin/skillopt/PerfView.vue'
import { getSkillFitDefinitionSummary } from '@/api/aiSkills'
import type { SkillFitDefinitionSummary } from '@/api/aiSkills'
import { classifyHealth } from '@/components/admin/skillopt/fitView'

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

// ── 任务拟合：主从状态 ──────────────────────────────────────────────

const fitLoading = ref(false)
const fitError = ref('')
const definitions = ref<SkillFitDefinitionSummary[]>([])
const selected = ref<SkillFitDefinitionSummary | null>(null)
const globalPatternsVisible = ref(false)

/** 需要人工介入的定义数（偏离 / 部分符合 / 未配置步骤） */
const attentionCount = computed(() =>
  definitions.value.filter(d => classifyHealth(d).rank <= 2).length)
const totalRuns = computed(() =>
  definitions.value.reduce((s, d) => s + (d.tasks || 0), 0))

async function loadDefinitions() {
  fitLoading.value = true
  fitError.value = ''
  try {
    const res = await getSkillFitDefinitionSummary()
    definitions.value = res.definitions || []
    // 选中项刷新后按 (defKind, defName) 重新对齐；无选中/已消失则取第一个
    const cur = selected.value
    const found = cur
      ? definitions.value.find(
          d => d.defKind === cur.defKind && d.defName === cur.defName)
      : undefined
    selected.value = found ?? definitions.value[0] ?? null
  } catch (e: unknown) {
    fitError.value = errMsg(e)
  } finally {
    fitLoading.value = false
  }
}
function refresh() {
  void load()
  void loadDefinitions()
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
function shortHash(h?: string | null) {
  return h ? h.slice(0, 10) + '…' : '—'
}
function fmtTime(v?: string | null) {
  return v ? new Date(v).toLocaleString() : '—'
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

.fit-summary {
  display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  padding: 10px 14px; margin-bottom: 14px; border-radius: 6px;
  background: var(--el-fill-color-light); font-size: 13px;
}
.fit-summary b { font-size: 16px; font-variant-numeric: tabular-nums; margin-right: 2px; }
.fit-summary .is-bad b { color: var(--el-color-danger); }
.fit-summary__top { margin-left: auto; }
.fit-layout { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 20px; align-items: start; }
.fit-layout__side { position: sticky; top: 8px; }
.fit-layout__main { min-width: 0; }
.fit-empty { max-width: 560px; margin: 40px auto; }
.fit-empty__title { font-size: 15px; font-weight: 600; margin: 0 0 10px; text-align: center; }
.fit-empty__steps { margin: 0; padding-left: 20px; font-size: 13px; line-height: 1.9; color: var(--el-text-color-regular); }
.fit-empty code { font-size: 12px; background: var(--el-fill-color); padding: 1px 4px; border-radius: 3px; }
@media (max-width: 960px) {
  .fit-layout { grid-template-columns: minmax(0, 1fr); }
  .fit-layout__side { position: static; }
}
</style>
