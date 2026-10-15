<template>
  <div class="smx">
    <p v-if="!steps.length" class="smx__empty">
      暂无步骤明细。定义配置了 fit.steps 并完成一次运行后，这里会按步骤展示命中情况。
    </p>
    <div v-else class="smx__scroll">
      <table class="smx__table">
        <thead>
          <tr>
            <th class="smx__step-h">步骤</th>
            <th class="smx__rate-h">命中率</th>
            <th v-for="r in columns" :key="r.attemptId" class="smx__run-h"
                :title="fmtTime(r.computedAt)">
              {{ shortTime(r.computedAt) }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in steps" :key="s.id">
            <td class="smx__step" :title="s.id">{{ s.name }}</td>
            <td class="smx__rate">
              <span v-if="rateOf(s.id) == null" class="muted">—</span>
              <span v-else :class="rateClass(rateOf(s.id)!)">{{ fmtRate(rateOf(s.id)) }}</span>
            </td>
            <td v-for="r in columns" :key="r.attemptId" class="smx__cell-wrap">
              <button v-if="cellOf(r, s.id)" type="button" class="smx__cell"
                      :class="`is-${cellOf(r, s.id)}`"
                      :title="`${fmtTime(r.computedAt)} · ${stepStatusText(cellOf(r, s.id) ?? '')}`"
                      @click="emit('open-run', r.attemptId)" />
              <span v-else class="smx__cell is-none" :title="'该次运行无此步骤明细'" />
            </td>
          </tr>
        </tbody>
      </table>
      <div class="smx__legend">
        <span><i class="smx__cell is-hit" /> 命中</span>
        <span><i class="smx__cell is-miss" /> 未执行</span>
        <span><i class="smx__cell is-out_of_order" /> 顺序不符</span>
        <span><i class="smx__cell is-skipped" /> 跳过</span>
        <span><i class="smx__cell is-none" /> 无数据</span>
        <span class="muted">点击色块可打开对应运行详情</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { SkillFitDetail } from '@/api/aiSkills'
import { fmtRate, fmtTime, stepHitRate, stepStatusIn, stepStatusText } from './fitView'
import type { StepColumn } from './fitView'

const props = defineProps<{
  steps: StepColumn[]
  /** 按时间倒序（最新在前）的运行明细 */
  runs: SkillFitDetail[]
}>()
const emit = defineEmits<{ (e: 'open-run', attemptId: string): void }>()

const MAX_COLUMNS = 10
/** 最近 N 次运行，按时间正序（左旧右新） */
const columns = computed(() => props.runs.slice(0, MAX_COLUMNS).reverse())

function rateOf(stepId: string) {
  return stepHitRate(props.runs, stepId)
}
function cellOf(r: SkillFitDetail, stepId: string) {
  return stepStatusIn(r, stepId)
}
function rateClass(v: number) {
  if (v >= 0.9) return 'rate-good'
  if (v >= 0.6) return 'rate-warn'
  return 'rate-bad'
}
function shortTime(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
</script>

<style scoped>
.smx__empty { color: var(--el-text-color-secondary); font-size: 12.5px; margin: 0; }
.smx__scroll { overflow-x: auto; }
.smx__table { border-collapse: collapse; font-size: 12.5px; }
.smx__table th, .smx__table td { padding: 6px 8px; border-bottom: 1px solid var(--el-border-color-lighter); }
.smx__table th { font-weight: 500; color: var(--el-text-color-secondary); font-size: 12px; text-align: center; white-space: nowrap; }
.smx__step-h, .smx__step { text-align: left !important; }
.smx__step { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.smx__rate, .smx__rate-h { width: 72px; text-align: center; font-variant-numeric: tabular-nums; font-weight: 600; }
.rate-good { color: var(--el-color-success); }
.rate-warn { color: var(--el-color-warning); }
.rate-bad { color: var(--el-color-danger); }
.smx__cell-wrap { text-align: center; }
.smx__cell {
  display: inline-block; width: 16px; height: 16px; border-radius: 3px;
  border: none; padding: 0; cursor: pointer; vertical-align: middle;
}
.smx__cell:focus-visible { outline: 2px solid var(--el-color-primary); }
.smx__cell.is-hit { background: var(--el-color-success); }
.smx__cell.is-miss { background: var(--el-color-danger); }
.smx__cell.is-out_of_order { background: var(--el-color-warning); }
.smx__cell.is-skipped { background: var(--el-border-color-darker); opacity: .5; }
.smx__cell.is-none { background: transparent; border: 1px dashed var(--el-border-color); cursor: default; }
.smx__legend {
  display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px;
  font-size: 12px; color: var(--el-text-color-regular); align-items: center;
}
.smx__legend .smx__cell { width: 11px; height: 11px; margin-right: 4px; cursor: default; }
.muted { color: var(--el-text-color-secondary); }
</style>
