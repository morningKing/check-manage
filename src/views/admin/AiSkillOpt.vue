<template>
  <div class="skillopt">
    <div class="skillopt__header">
      <span class="skillopt__title">SkillOpt — 技能优化</span>
      <ElTag v-if="sourceSummary" size="small" type="info">
        共 {{ sourceSummary.skills }} 个技能 · {{ sourceSummary.invocations }} 次调用
      </ElTag>
      <ElButton size="small" @click="load">刷新</ElButton>
    </div>
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
          <ElTableColumn prop="skill" label="技能" min-width="180" />
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
          <h4 class="skillopt__sec">版本对比 — {{ selectedSkill.skill }}</h4>
          <p v-if="!versionsFor(selectedSkill.skill).length" class="muted">该技能只有一个版本或无数据。</p>
          <ElTable v-else :data="versionsFor(selectedSkill.skill)" size="small">
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
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElTable, ElTableColumn, ElTag, ElAlert, ElButton } from 'element-plus'
import { get } from '@/utils/request'

interface SkillRow {
  skill: string
  skill_hash: string
  invocations: number
  completed: number
  failed: number
  runtime_confirmed: number
  heuristic: number
  completion_rate: number | null
  completion_rate_delta?: number | null
}

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
  if (row) loadVersions(row.skill)
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
    const ax = e as { response?: { data?: { error?: string } }; message?: string }
    error.value = ax?.response?.data?.error || ax?.message || '加载失败'
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.skillopt__header { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.skillopt__title { font-size: 16px; font-weight: 700; color: #1a237e; }
.skillopt__desc { color: var(--el-text-color-secondary); font-size: 12.5px; margin: 4px 0 14px; }
.skillopt__sec { font-size: 14px; margin: 18px 0 8px; }
.mono { font-family: monospace; font-size: 12px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.delta-up { color: var(--el-color-success); font-weight: 600; }
.delta-down { color: var(--el-color-danger); font-weight: 600; }
</style>
