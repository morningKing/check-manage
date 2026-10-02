<template>
  <div class="def-ov">
    <div class="def-ov__head">
      <strong class="def-ov__name">{{ def.defName }}</strong>
      <ElTag size="small" :type="def.defKind === 'skill' ? 'success' : 'primary'">
        {{ kindText(def.defKind) }}
      </ElTag>
      <span class="muted">
        最新版本 {{ def.latestLabel || shortHash(def.latestHash) }}
      </span>
    </div>
    <div class="def-ov__cards">
      <div class="def-ov__card"><b>{{ def.tasks }}</b><span>任务</span></div>
      <div class="def-ov__card"><b>{{ fmtRate(def.fitRate) }}</b><span>拟合率</span></div>
      <div class="def-ov__card">
        <b :class="def.divergedCount + def.partialCount ? 'is-bad' : ''">
          {{ def.divergedCount + def.partialCount }}
        </b><span>偏离</span>
      </div>
      <div class="def-ov__card"><b>{{ def.versions }}</b><span>版本</span></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElTag } from 'element-plus'
import type { SkillFitDefinitionSummary } from '@/api/aiSkills'

defineProps<{ def: SkillFitDefinitionSummary }>()

function kindText(kind: string) {
  if (kind === 'skill') return '技能'
  if (kind === 'agent') return '代理'
  return kind
}
function fmtRate(v: number | null | undefined) {
  return v == null ? '—' : Math.round(v * 100) + '%'
}
function shortHash(h?: string | null) {
  return h ? h.slice(0, 10) + '…' : '—'
}
</script>

<style scoped>
.def-ov__head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.def-ov__name { font-size: 15px; }
.def-ov__cards { display: flex; gap: 10px; margin-bottom: 4px; }
.def-ov__card {
  flex: 1; padding: 10px 12px; border-radius: 6px;
  background: var(--el-fill-color-light);
  display: flex; flex-direction: column; gap: 2px;
}
.def-ov__card b { font-size: 18px; font-variant-numeric: tabular-nums; }
.def-ov__card b.is-bad { color: var(--el-color-danger); }
.def-ov__card span { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
