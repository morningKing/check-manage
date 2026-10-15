<template>
  <div class="fit-defs">
    <ElInput v-model="keyword" size="small" clearable placeholder="搜索技能 / 代理名称" />
    <ElRadioGroup v-model="filter" size="small" class="fit-defs__filter">
      <ElRadioButton value="all">全部 {{ counts.all }}</ElRadioButton>
      <ElRadioButton value="attention">需关注 {{ counts.attention }}</ElRadioButton>
    </ElRadioGroup>

    <p v-if="!visible.length" class="fit-defs__empty">
      {{ filter === 'attention' ? '没有需要关注的定义' : '没有匹配的定义' }}
    </p>
    <div class="fit-defs__list">
      <button v-for="d in visible" :key="d.defKind + '/' + d.defName" type="button"
              class="fit-def" :class="{ 'is-active': isActive(d) }"
              @click="emit('select', d)">
        <div class="fit-def__head">
          <span class="fit-def__name" :title="d.defName">{{ d.defName }}</span>
          <ElTag size="small" effect="plain" :type="healthTagType(d)">
            {{ healthOf(d).label }}
          </ElTag>
        </div>
        <div class="fit-def__meta">
          <span>{{ kindLabel(d.defKind) }}</span>
          <span>·</span>
          <span v-if="d.tasks">{{ d.tasks }} 次运行 · 符合率 {{ fmtRate(d.fitRate) }}</span>
          <span v-else>暂无运行</span>
        </div>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElInput, ElRadioGroup, ElRadioButton, ElTag } from 'element-plus'
import type { SkillFitDefinitionSummary } from '@/api/aiSkills'
import { classifyHealth, fmtRate, kindLabel } from './fitView'
import type { Health } from './fitView'

const props = defineProps<{
  definitions: SkillFitDefinitionSummary[]
  selected: SkillFitDefinitionSummary | null
}>()
const emit = defineEmits<{ (e: 'select', d: SkillFitDefinitionSummary): void }>()

const keyword = ref('')
const filter = ref<'all' | 'attention'>('all')

/** 需要人工介入的：偏离 / 部分符合 / 未配置步骤 */
function needsAttention(d: SkillFitDefinitionSummary) {
  return classifyHealth(d).rank <= 2
}

/** 列表排序：需处理的在前；同组内按最近活动倒序 */
const sorted = computed(() => {
  return [...props.definitions].sort((a, b) => {
    const ra = classifyHealth(a).rank
    const rb = classifyHealth(b).rank
    if (ra !== rb) return ra - rb
    return (b.lastActivity ?? '').localeCompare(a.lastActivity ?? '')
  })
})

const visible = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return sorted.value.filter(d => {
    if (filter.value === 'attention' && !needsAttention(d)) return false
    if (kw && !d.defName.toLowerCase().includes(kw)) return false
    return true
  })
})

const counts = computed(() => ({
  all: props.definitions.length,
  attention: props.definitions.filter(needsAttention).length,
}))

function healthOf(d: SkillFitDefinitionSummary): Health {
  return classifyHealth(d)
}
function healthTagType(d: SkillFitDefinitionSummary): 'success' | 'warning' | 'danger' | 'info' {
  const tone = healthOf(d).tone
  if (tone === 'good') return 'success'
  if (tone === 'warn') return 'warning'
  if (tone === 'bad') return 'danger'
  return 'info'
}
function isActive(d: SkillFitDefinitionSummary) {
  return !!props.selected && props.selected.defKind === d.defKind
    && props.selected.defName === d.defName
}
</script>

<style scoped>
.fit-defs { display: flex; flex-direction: column; gap: 8px; }
.fit-defs__filter { display: flex; }
.fit-defs__filter :deep(.el-radio-button) { flex: 1; }
.fit-defs__filter :deep(.el-radio-button__inner) { width: 100%; }
.fit-defs__empty { color: var(--el-text-color-secondary); font-size: 12.5px; padding: 16px 0; text-align: center; }
.fit-defs__list { display: flex; flex-direction: column; gap: 4px; max-height: 620px; overflow-y: auto; }
.fit-def {
  all: unset; box-sizing: border-box; width: 100%;
  padding: 8px 10px; border-radius: 6px; cursor: pointer;
  border: 1px solid transparent;
}
.fit-def:hover { background: var(--el-fill-color-light); }
.fit-def:focus-visible { outline: 2px solid var(--el-color-primary-light-5); }
.fit-def.is-active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
}
.fit-def__head { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.fit-def__name {
  font-size: 13px; font-weight: 600; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.fit-def__meta {
  display: flex; align-items: center; gap: 6px; margin-top: 4px;
  font-size: 12px; color: var(--el-text-color-secondary);
}
</style>
