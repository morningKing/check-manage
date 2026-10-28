<template>
  <div class="rdp" data-testid="run-diag-panel">
    <div class="rdp__bar">
      <span class="rdp__title">运行诊断</span>
      <el-tag v-if="data" :type="overallTag()" size="small">{{ overallText() }}</el-tag>
      <el-button
        size="small" :loading="loading" data-testid="run-diag-generate"
        @click="generate">生成/刷新</el-button>
    </div>
    <div v-if="data" class="rdp__dims">
      <div
        v-for="key in ORDER" :key="key"
        class="rd-dim" :data-status="data.dimensions[key]?.status || 'uncertain'">
        <span class="rd-dim__name">{{ LABELS[key] }}</span>
        <el-tag size="small" :type="tagType(data.dimensions[key]?.status)">
          {{ statusText(data.dimensions[key]?.status) }}</el-tag>
        <span class="rd-dim__summary">{{ data.dimensions[key]?.summary }}</span>
      </div>
    </div>
    <div v-else class="rdp__empty">尚未生成诊断</div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { generateRunDiagnosis, getRunDiagnosis } from '@/api/aiSessionAdmin'

const props = defineProps<{ attemptId: string }>()
const ORDER = ['task_result', 'rule_compliance', 'promise_action', 'skill_fit', 'performance'] as const
const LABELS: Record<string, string> = {
  task_result: '任务结果', rule_compliance: '规则遵从',
  promise_action: '承诺—行动一致性', skill_fit: '技能拟合', performance: '性能',
}
const data = ref<any>(null)
const loading = ref(false)

const OVERALL_TEXT: Record<string, string> = {
  success: '成功', partial: '部分成功', fail: '失败', uncertain: '证据不足',
}
const overallText = () => OVERALL_TEXT[data.value?.overall] || data.value?.overall
const overallTag = () =>
  ({ success: 'success', partial: 'warning', fail: 'danger', uncertain: 'info' } as any)[data.value?.overall] || 'info'
const tagType = (s?: string) =>
  ({ pass: 'success', fail: 'danger', uncertain: 'info' } as any)[s || 'uncertain'] || 'info'
const statusText = (s?: string) =>
  ({ pass: '通过', fail: '未通过', uncertain: '证据不足' } as any)[s || 'uncertain'] || s

async function load() {
  try {
    const resp: any = await getRunDiagnosis(props.attemptId)
    data.value = resp.data ?? resp
  } catch { data.value = null }
}

async function generate() {
  loading.value = true
  try {
    const resp: any = await generateRunDiagnosis(props.attemptId)
    data.value = resp.data ?? resp
  } finally {
    loading.value = false
  }
}

load()
</script>

<style scoped>
.rdp { border: 1px solid var(--el-border-color-lighter); border-radius: 6px; padding: 8px 12px; }
.rdp__bar { display: flex; align-items: center; gap: 8px; }
.rdp__title { font-weight: 600; margin-right: auto; }
.rdp__empty { color: var(--el-text-color-secondary); font-size: 12px; padding: 6px 0; }
.rd-dim { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px; }
.rd-dim__name { width: 110px; color: var(--el-text-color-regular); }
.rd-dim__summary { color: var(--el-text-color-secondary); }
.rd-dim[data-status='fail'] .rd-dim__summary { color: var(--el-color-danger); }
</style>
