<template>
  <div class="admin-gate" data-test="child-gate">
    <div v-if="loading" class="admin-gate__hint">加载中…</div>
    <template v-else-if="gate">
      <div class="admin-gate__head">
        <span class="admin-gate__label">门禁终态</span>
        <el-tag size="small" :type="statusTagType" data-test="gate-status">
          {{ statusLabel }}</el-tag>
        <span v-if="gate.gateError" class="admin-gate__error" data-test="gate-error">
          {{ gate.gateError }}</span>
      </div>
      <el-alert v-if="!gate.expectations.length" type="info" :closable="false"
                title="该子任务没有登记门禁期望（未引用策略且批未配置动作检查）" />
      <el-table v-else :data="gate.expectations" size="small" data-test="gate-table">
        <el-table-column prop="name" label="规则" min-width="130"
                         show-overflow-tooltip />
        <el-table-column label="类型" width="128">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="modeTag(row)">
              {{ modeLabel(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="触发" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <code class="admin-gate__trig">{{ row.tool }} ~ /{{ row.argsPattern }}/</code>
          </template>
        </el-table-column>
        <el-table-column label="命中" width="90">
          <template #default="{ row }">
            <span v-if="row.mode === 'checkpoint'" data-test="gate-fired">
              {{ row.firedCount }} / {{ row.maxChecks }}</span>
            <span v-else class="admin-gate__muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="150">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="resultTag(row.lastStatus)">
              {{ resultLabel(row) }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElTag, ElTable, ElTableColumn, ElAlert } from 'element-plus'
import type { AdminChildGate, AdminGateExpectation } from '@/api/aiBatchAdmin'

// 纯渲染：数据由父层取（getAdminChildGate），组件的映射逻辑可单测。
const props = defineProps<{
  gate: AdminChildGate | null
  loading: boolean
}>()

const statusLabel = computed(() => {
  const s = props.gate?.gateStatus
  if (s === 'passed') return '已通过'
  if (s === 'failed') return '未通过'
  if (s === 'inconclusive') return '无法判定'
  return '未核对'
})
const statusTagType = computed(() => {
  const s = props.gate?.gateStatus
  if (s === 'passed') return 'success'
  if (s === 'failed') return 'danger'
  if (s === 'inconclusive') return 'warning'
  return 'info'
})

/** 三型标签：pre=拦截（前置）/ checkpoint=过程检查（前/后）/ post=终态核对 */
function modeLabel(row: AdminGateExpectation): string {
  if (row.mode === 'pre') return '拦截（前置）'
  if (row.mode === 'checkpoint') {
    return row.timing === 'after' ? '过程检查（后）' : '过程检查（前）'
  }
  return '终态核对'
}
function modeTag(row: AdminGateExpectation): 'danger' | 'warning' | 'success' {
  if (row.mode === 'pre') return 'danger'
  if (row.mode === 'checkpoint') return 'warning'
  return 'success'
}
function resultLabel(row: AdminGateExpectation): string {
  if (row.lastStatus === 'passed') {
    return row.lastEvidence != null ? `已通过（证据 ${row.lastEvidence}）` : '已通过'
  }
  if (row.lastStatus === 'failed') return '未通过'
  return '待核对'
}
function resultTag(s: string | null): 'success' | 'danger' | 'info' {
  if (s === 'passed') return 'success'
  if (s === 'failed') return 'danger'
  return 'info'
}
</script>

<style scoped lang="scss">
.admin-gate__hint { padding: 12px 0; color: var(--el-text-color-secondary); }
.admin-gate__head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.admin-gate__label { font-size: 13px; color: var(--el-text-color-secondary); }
.admin-gate__error { font-size: 12px; color: var(--el-color-danger); }
.admin-gate__trig { font-size: 12px; }
.admin-gate__muted { color: var(--el-text-color-placeholder); }
</style>
