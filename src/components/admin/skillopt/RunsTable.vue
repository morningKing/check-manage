<template>
  <div class="runs">
    <p v-if="!runs.length" class="runs__empty">
      {{ filter ? '当前筛选下没有运行记录' : '该定义还没有运行记录' }}
    </p>
    <template v-else>
      <div class="runs__bar">
        <ElRadioGroup v-model="filter" size="small">
          <ElRadioButton value="">全部</ElRadioButton>
          <ElRadioButton value="fit">符合</ElRadioButton>
          <ElRadioButton value="partial">部分符合</ElRadioButton>
          <ElRadioButton value="diverged">偏离</ElRadioButton>
          <ElRadioButton value="other">其他</ElRadioButton>
        </ElRadioGroup>
      </div>
      <ElTable :data="visible" size="small" row-class-name="runs__row"
               @row-click="(row: SkillFitRow) => emit('open-run', row.attemptId)">
        <ElTableColumn label="时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.computedAt) }}</template>
        </ElTableColumn>
        <ElTableColumn label="执行者" min-width="130">
          <template #default="{ row }">
            <span :title="row.sessionId">{{ runTitle(row) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn label="结果" width="110">
          <template #default="{ row }">
            <ElTag size="small" effect="light" :type="tagType(row.status)">
              {{ statusMeta(row.status).label }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="得分" width="70" align="center">
          <template #default="{ row }">
            <span v-if="row.stepsTotal" class="runs__score">{{ row.score }}</span>
            <span v-else class="muted">—</span>
          </template>
        </ElTableColumn>
        <ElTableColumn label="未执行的步骤" min-width="200">
          <template #default="{ row }">
            <span v-if="missing[row.attemptId]" class="runs__miss">
              {{ missing[row.attemptId] }}
            </span>
            <span v-else class="muted">{{ row.stepsTotal ? '无' : statusMeta(row.status).hint }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn width="64" align="center">
          <template #default="{ row }">
            <ElButton link type="primary" size="small"
                      @click.stop="emit('open-run', row.attemptId)">详情</ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElTable, ElTableColumn, ElTag, ElButton, ElRadioGroup, ElRadioButton } from 'element-plus'
import type { SkillFitRow } from '@/api/aiSkills'
import { fmtTime, runTitle, statusMeta } from './fitView'
import type { Tone } from './fitView'

const props = defineProps<{
  runs: SkillFitRow[]
  /** attemptId → 未执行步骤的摘要文案（明细加载后才有） */
  missing: Record<string, string>
}>()
const emit = defineEmits<{ (e: 'open-run', attemptId: string): void }>()

const filter = ref('')
const OTHER = ['failed', 'inconclusive', 'no_trace', 'parse_error', 'no_steps']
const visible = computed(() => {
  if (!filter.value) return props.runs
  if (filter.value === 'other') return props.runs.filter(r => OTHER.includes(r.status))
  return props.runs.filter(r => r.status === filter.value)
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
.runs__bar { margin-bottom: 8px; }
.runs__empty { color: var(--el-text-color-secondary); font-size: 12.5px; padding: 16px 0; text-align: center; margin: 0; }
.runs__score { font-weight: 600; font-variant-numeric: tabular-nums; }
.runs__miss { color: var(--el-color-danger); font-size: 12.5px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
:deep(.runs__row) { cursor: pointer; }
</style>
