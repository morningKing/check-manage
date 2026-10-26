<template>
  <div class="cause-dist" data-test="cause-dist">
    <div class="cause-dist__head">
      <span class="cause-dist__title">慢因分布</span>
      <span class="muted">{{ dist.taskCount }} 个已完结任务</span>
    </div>
    <template v-if="dist.taskCount > 0">
      <div class="cause-bar" data-test="cause-bar">
        <div v-for="seg in segs" :key="seg" class="cause-bar__seg"
             :data-test="`cause-seg-${seg}`"
             :style="{ width: segPct(seg), background: SEGMENT_META[seg].color }" />
      </div>
      <div class="cause-legend" data-test="cause-legend">
        <span v-for="seg in segs" :key="seg" class="cause-legend__item">
          <i class="dot" :style="{ background: SEGMENT_META[seg].color }" />
          {{ SEGMENT_META[seg].label }} {{ pct(ratio(seg)) }} · {{ fmtMs(sharesOf(seg)) }}
        </span>
      </div>
      <div class="cause-counts">
        <span class="muted">主导慢因（点击筛选任务）：</span>
        <button v-for="seg in nonzeroSegs" :key="seg" type="button"
                class="cause-chip" :class="{ active: active === seg }"
                :data-test="`cause-chip-${seg}`" @click="emit('toggle', seg)">
          <i class="dot" :style="{ background: SEGMENT_META[seg].color }" />
          {{ SEGMENT_META[seg].label }} {{ dist.dominantCounts[seg] }} 个
        </button>
      </div>
    </template>
    <div v-else class="muted" data-test="cause-empty">
      暂无已完结任务——执行后自动汇总慢因分布。
    </div>
    <div class="muted cause-note">
      口径：与任务抽屉覆盖占比条同源——四段互斥、之和 = 墙钟，工具段已从模型
      活跃中拆出；主导慢因 = 任务内占比最大的段。
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PerfCauseDistribution, PerfCauseSegment } from '@/api/aiSkills'
import { fmtMs, pct, SEGMENT_META } from './format'

const props = defineProps<{ dist: PerfCauseDistribution;
                            active?: PerfCauseSegment | null }>()
const emit = defineEmits<{ (e: 'toggle', seg: PerfCauseSegment): void }>()

const segs = Object.keys(SEGMENT_META) as PerfCauseSegment[]
const nonzeroSegs = segs.filter(s => props.dist.dominantCounts[s] > 0)

const MS_KEY = { model: 'modelMs', wait: 'subagentWaitMs',
                 tool: 'toolMs', idle: 'idleMs' } as const
function sharesOf(seg: PerfCauseSegment) {
  return props.dist.shares[MS_KEY[seg]]
}
function ratio(seg: PerfCauseSegment) {
  return props.dist.shares.wallMs ? sharesOf(seg) / props.dist.shares.wallMs : 0
}
function segPct(seg: PerfCauseSegment) {
  const w = props.dist.shares.wallMs
  return w ? `${(sharesOf(seg) / w * 100).toFixed(2)}%` : '0%'
}
</script>

<style scoped>
.cause-dist { border: 1px solid var(--el-border-color-lighter); border-radius: 8px;
  padding: 10px 14px; margin: 12px 0; }
.cause-dist__head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.cause-dist__title { font-weight: 600; font-size: 14px; }
.cause-bar { display: flex; height: 16px; border-radius: 8px; overflow: hidden;
  background: var(--el-fill-color); }
.cause-bar__seg { height: 100%; }
.cause-legend { display: flex; gap: 16px; flex-wrap: wrap; margin: 8px 0 6px; font-size: 12.5px; }
.cause-counts { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12.5px; }
.cause-chip { display: inline-flex; align-items: center; gap: 5px; padding: 2px 10px;
  border: 1px solid var(--el-border-color); border-radius: 12px; background: transparent;
  cursor: pointer; font-size: 12.5px; color: var(--el-text-color-regular); }
.cause-chip:hover { background: var(--el-fill-color-light); }
.cause-chip.active { border-color: var(--el-color-primary); color: var(--el-color-primary);
  background: var(--el-color-primary-light-9); }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 2px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.cause-note { margin-top: 8px; line-height: 1.6; }
</style>
