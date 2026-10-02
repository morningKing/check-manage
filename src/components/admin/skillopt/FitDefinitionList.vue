<template>
  <div class="fit-defs">
    <div v-for="d in definitions" :key="d.defKind + '/' + d.defName"
         class="fit-def" :class="{ 'is-active': isActive(d) }"
         @click="emit('select', d)">
      <div class="fit-def__head">
        <ElTag size="small" :type="d.defKind === 'skill' ? 'success' : 'primary'">
          {{ kindText(d.defKind) }}
        </ElTag>
        <span class="fit-def__name" :title="d.defName">{{ d.defName }}</span>
      </div>
      <div class="fit-def__meta">
        <span>拟合率 {{ fmtRate(d.fitRate) }}</span>
        <span>{{ d.versions }} 版</span>
        <ElTag v-if="d.divergedCount + d.partialCount" size="small" type="danger">
          偏离 {{ d.divergedCount + d.partialCount }}
        </ElTag>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElTag } from 'element-plus'
import type { SkillFitDefinitionSummary } from '@/api/aiSkills'

const props = defineProps<{
  definitions: SkillFitDefinitionSummary[]
  selected: SkillFitDefinitionSummary | null
}>()
const emit = defineEmits<{ (e: 'select', d: SkillFitDefinitionSummary): void }>()

function isActive(d: SkillFitDefinitionSummary) {
  return !!props.selected && props.selected.defKind === d.defKind
    && props.selected.defName === d.defName
}
function kindText(kind: string) {
  if (kind === 'skill') return '技能'
  if (kind === 'agent') return '代理'
  return kind
}
function fmtRate(v: number | null | undefined) {
  return v == null ? '—' : Math.round(v * 100) + '%'
}
</script>

<style scoped>
.fit-defs { display: flex; flex-direction: column; gap: 4px; }
.fit-def {
  padding: 8px 10px; border-radius: 6px; cursor: pointer;
  border: 1px solid transparent;
}
.fit-def:hover { background: var(--el-fill-color-light); }
.fit-def.is-active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
}
.fit-def__head { display: flex; align-items: center; gap: 6px; }
.fit-def__name {
  font-size: 13px; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.fit-def__meta {
  display: flex; align-items: center; gap: 8px; margin-top: 4px;
  font-size: 12px; color: var(--el-text-color-secondary);
}
</style>
