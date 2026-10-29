<template>
  <ElDrawer :model-value="visible" :title="title" size="620px" append-to-body
            @update:model-value="emit('update:visible', $event)">
    <div v-if="row" class="rd">
      <div class="rd__summary">
        <ElTag effect="light" :type="tagType(row.status)">{{ statusMeta(row.status).label }}</ElTag>
        <span v-if="row.stepsTotal" class="rd__score">得分 {{ row.score }}</span>
        <span class="rd__meta">{{ fmtTime(row.computedAt) }} · {{ runTitle(row) }}</span>
      </div>
      <p class="rd__hint">{{ statusMeta(row.status).hint }}</p>

      <section v-if="detail" class="rd__sec">
        <h4>步骤执行情况</h4>
        <p v-if="!detail.perStep.length" class="rd__muted">
          该定义没有步骤明细（未配置 fit.steps，或本次运行没有可用轨迹）。
        </p>
        <ElTable v-else :data="detail.perStep" size="small">
          <ElTableColumn prop="name" label="步骤" min-width="130" />
          <ElTableColumn label="结果" width="90">
            <template #default="{ row: ps }">
              <span class="rd__dot" :class="`is-${ps.status}`" />{{ stepStatusText(ps.status) }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="依据（工具调用）" min-width="220">
            <template #default="{ row: ps }">
              <span v-if="ps.evidence && ps.evidence.length" class="rd__mono"
                    :title="ps.evidence[0].args">
                {{ ps.evidence[0].tool }} · {{ trunc(ps.evidence[0].args, 80) }}
              </span>
              <span v-else class="rd__muted">—</span>
            </template>
          </ElTableColumn>
        </ElTable>
      </section>

      <section v-if="subagentFits.length" class="rd__sec">
        <h4>子代理执行情况</h4>
        <p class="rd__muted">子代理各自独立判定，不并入上面的父级结果。</p>
        <ElTable :data="subagentFits" size="small">
          <ElTableColumn label="子代理" width="120">
            <template #default="{ row: sf }">{{ sf.subtaskAgent || '—' }}</template>
          </ElTableColumn>
          <ElTableColumn prop="defName" label="定义" min-width="120" />
          <ElTableColumn label="结果" width="110">
            <template #default="{ row: sf }">
              <ElTag size="small" effect="light" :type="tagType(sf.status)">
                {{ statusMeta(sf.status).label }}
              </ElTag>
            </template>
          </ElTableColumn>
        </ElTable>
      </section>

      <section class="rd__sec">
        <h4>偏差诊断</h4>
        <template v-if="diagnosis">
          <p class="rd__cause">
            主要原因：
            <ElTag size="small" :type="causeTagType(diagnosis.cause)">
              {{ causeLabel(diagnosis.cause) }}
            </ElTag>
          </p>
          <ol v-if="diagnosis.suggestions.length" class="rd__sugs">
            <li v-for="(s, i) in diagnosis.suggestions" :key="i">{{ s }}</li>
          </ol>
          <div class="rd__cand">
            <ElButton size="small" type="primary" plain data-testid="cand-create"
                      @click="emit('create-candidate')">生成候选</ElButton>
            <span class="rd__cand-hint">进入候选区 → 回归验证 → 人工发布（不再直接生效）</span>
          </div>
          <div v-if="diagnosis.revised_steps && diagnosis.revised_steps.length" class="rd__rev">
            <div class="rd__rev-head">
              <span>建议的修订步骤（{{ diagnosis.revised_steps.length }} 步）</span>
              <ElButton link size="small" @click="emit('copy-revised', diagnosis)">复制 JSON</ElButton>
            </div>
            <pre class="rd__json">{{ JSON.stringify(diagnosis.revised_steps, null, 2) }}</pre>
          </div>
        </template>
        <template v-else>
          <p v-if="canDiagnose" class="rd__muted">
            尚未分析。分析会对照定义与实际轨迹，给出偏差原因和修订建议。
          </p>
          <p v-else class="rd__muted">该次运行结果正常，无需分析偏差。</p>
          <ElButton v-if="canDiagnose" size="small" type="warning"
                    :loading="diagnosing" @click="emit('diagnose')">分析偏差原因</ElButton>
        </template>
      </section>
    </div>

    <template #footer>
      <div class="rd__footer">
        <ElButton size="small" :loading="recomputing" @click="emit('recompute')">
          重新计算
        </ElButton>
        <ElButton size="small" type="primary" plain @click="emit('configure', row?.attemptId ?? '')">
          用此次运行调整步骤
        </ElButton>
      </div>
    </template>
  </ElDrawer>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElDrawer, ElTable, ElTableColumn, ElTag, ElButton } from 'element-plus'
import type { SkillFitDetail, SkillFitDiagnosis, SkillFitRow } from '@/api/aiSkills'
import {
  causeLabel, causeTagType, fmtTime, statusMeta, stepStatusText, runTitle, trunc,
} from './fitView'
import type { Tone } from './fitView'

const props = defineProps<{
  visible: boolean
  row: SkillFitRow | null
  /** 本定义在该次运行中的明细（含 perStep） */
  detail: SkillFitDetail | null
  subagentFits: SkillFitDetail[]
  diagnosis: SkillFitDiagnosis | null
  recomputing: boolean
  diagnosing: boolean
}>()
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'recompute'): void
  (e: 'diagnose'): void
  (e: 'copy-revised', d: SkillFitDiagnosis): void
  (e: 'configure', attemptId: string): void
  (e: 'create-candidate'): void
}>()

const title = computed(() => `运行详情 · ${props.row?.defName ?? ''}`)
const canDiagnose = computed(() => {
  const s = props.row?.status
  return s === 'partial' || s === 'diverged'
})

function tagType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  const tone: Tone = statusMeta(status).tone
  if (tone === 'good') return 'success'
  if (tone === 'warn') return 'warning'
  if (tone === 'bad') return 'danger'
  return 'info'
}
</script>

<style scoped>
.rd__summary { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rd__score { font-weight: 600; font-variant-numeric: tabular-nums; }
.rd__meta { font-size: 12px; color: var(--el-text-color-secondary); }
.rd__hint { font-size: 12.5px; color: var(--el-text-color-regular); margin: 6px 0 0; }
.rd__sec { margin-top: 18px; }
.rd__sec h4 { margin: 0 0 8px; font-size: 14px; }
.rd__muted { color: var(--el-text-color-secondary); font-size: 12.5px; margin: 0 0 8px; }
.rd__mono { font-family: monospace; font-size: 12px; }
.rd__dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.rd__dot.is-hit { background: var(--el-color-success); }
.rd__dot.is-miss { background: var(--el-color-danger); }
.rd__dot.is-out_of_order { background: var(--el-color-warning); }
.rd__dot.is-skipped { background: var(--el-border-color-darker); opacity: .6; }
.rd__cause { font-size: 13px; margin: 0 0 6px; }
.rd__cand { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
.rd__cand-hint { color: var(--el-text-color-secondary); font-size: 12px; }
.rd__sugs { margin: 4px 0 8px; padding-left: 20px; font-size: 12.5px; }
.rd__sugs li { margin: 3px 0; }
.rd__rev { margin-top: 8px; }
.rd__rev-head { display: flex; align-items: center; justify-content: space-between; font-size: 12.5px; margin-bottom: 4px; }
.rd__json {
  margin: 0; padding: 8px 10px; font-size: 12px; line-height: 1.5;
  background: var(--el-fill-color-darker); border-radius: 4px;
  max-height: 260px; overflow: auto; white-space: pre-wrap; word-break: break-all;
}
.rd__footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
