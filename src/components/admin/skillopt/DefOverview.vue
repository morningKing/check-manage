<template>
  <div class="def-ov">
    <div class="def-ov__top">
      <div class="def-ov__title">
        <strong class="def-ov__name">{{ def.defName }}</strong>
        <ElTag size="small" effect="plain" :type="def.defKind === 'skill' ? 'success' : 'primary'">
          {{ kindLabel(def.defKind) }}
        </ElTag>
        <span class="def-ov__ver">
          当前版本：{{ def.latestLabel || shortHash(def.latestHash) }}
        </span>
      </div>
      <ElButton :type="action.type" size="default" @click="onAction">
        {{ action.text }}
      </ElButton>
    </div>

    <p class="def-ov__verdict" :class="`is-${health.tone}`">
      {{ verdict }}
    </p>

    <div class="def-ov__cards">
      <div class="def-ov__card">
        <b>{{ def.tasks }}</b><span>累计运行</span>
      </div>
      <div class="def-ov__card">
        <b>{{ fmtRate(def.fitRate) }}</b><span>符合率</span>
      </div>
      <div class="def-ov__card">
        <b :class="{ 'is-bad': def.divergedCount > 0 }">{{ def.divergedCount }}</b>
        <span>偏离</span>
      </div>
      <div class="def-ov__card">
        <b :class="{ 'is-warn': def.partialCount > 0 }">{{ def.partialCount }}</b>
        <span>部分符合</span>
      </div>
      <div class="def-ov__card">
        <b>{{ def.versions }}</b><span>版本</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElButton, ElTag } from 'element-plus'
import type { SkillFitDefinitionSummary } from '@/api/aiSkills'
import { classifyHealth, fmtRate, kindLabel, shortHash } from './fitView'

const props = defineProps<{ def: SkillFitDefinitionSummary }>()
const emit = defineEmits<{ (e: 'configure'): void; (e: 'open-issue'): void }>()

const health = computed(() => classifyHealth(props.def))

/** 一句话结论：管理员第一眼要知道的事 */
const verdict = computed(() => {
  switch (health.value.key) {
    case 'diverged':
      return '最近一次运行偏离定义，下方「运行记录」可查看具体哪些步骤没有执行。'
    case 'partial':
      return '最近一次运行部分符合定义，有步骤缺失或顺序不对。'
    case 'unconfigured':
      return '定义里没有 fit.steps，系统无法判断执行是否符合预期。先生成并保存步骤即可开始拟合。'
    case 'ok':
      return health.value.label === '正常'
        ? '最近一次运行符合定义。'
        : `最近一次运行结果为「${health.value.label}」，详见运行记录。`
    default:
      return '还没有运行记录。执行一次相关任务后，这里会显示拟合结果。'
  }
})

/** 唯一主操作：随健康度变化，避免管理员在多个按钮间犹豫 */
const action = computed<{ text: string; type: 'primary' | 'warning' | 'default'; kind: 'configure' | 'issue' }>(() => {
  switch (health.value.key) {
    case 'diverged':
    case 'partial':
      return { text: '查看偏离原因', type: 'warning', kind: 'issue' }
    case 'unconfigured':
    case 'idle':
      return { text: '配置步骤', type: 'primary', kind: 'configure' }
    default:
      return { text: '编辑步骤', type: 'default', kind: 'configure' }
  }
})

function onAction() {
  if (action.value.kind === 'issue') emit('open-issue')
  else emit('configure')
}
</script>

<style scoped>
.def-ov { display: flex; flex-direction: column; gap: 10px; }
.def-ov__top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.def-ov__title { display: flex; align-items: center; gap: 8px; min-width: 0; flex-wrap: wrap; }
.def-ov__name { font-size: 16px; }
.def-ov__ver { font-size: 12px; color: var(--el-text-color-secondary); }
.def-ov__verdict {
  margin: 0; padding: 8px 12px; border-radius: 6px; font-size: 13px;
  background: var(--el-fill-color-light); color: var(--el-text-color-regular);
}
.def-ov__verdict.is-bad { background: var(--el-color-danger-light-9); color: var(--el-color-danger); }
.def-ov__verdict.is-warn { background: var(--el-color-warning-light-9); color: var(--el-color-warning-dark-2); }
.def-ov__verdict.is-good { background: var(--el-color-success-light-9); color: var(--el-color-success); }
.def-ov__cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.def-ov__card {
  padding: 8px 10px; border-radius: 6px; border: 1px solid var(--el-border-color-lighter);
  display: flex; flex-direction: column; gap: 2px;
}
.def-ov__card b { font-size: 18px; font-variant-numeric: tabular-nums; }
.def-ov__card b.is-bad { color: var(--el-color-danger); }
.def-ov__card b.is-warn { color: var(--el-color-warning); }
.def-ov__card span { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
