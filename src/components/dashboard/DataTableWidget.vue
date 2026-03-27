<template>
  <div class="data-table-widget">
    <el-table :data="tableModel.rows" size="small" stripe max-height="100%" style="width: 100%">
      <el-table-column
        v-for="column in tableModel.columns"
        :key="column.key"
        :prop="column.key"
        :label="column.label"
        :min-width="column.align === 'right' ? 120 : 140"
        :align="column.align || 'left'"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          {{ formatCell(row[column.key]) }}
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AggregateResult } from '@/api/dashboard'
import { toTableModel } from './aggregateResult'

const props = withDefaults(defineProps<{
  result?: AggregateResult | null
  groupLabel?: string
  columnLabel?: string
  valueLabel?: string
  metricLabels?: Record<string, string>
}>(), {
  groupLabel: '分组',
  columnLabel: '系列',
  valueLabel: '值',
  metricLabels: () => ({}),
})

const tableModel = computed(() => toTableModel(props.result, {
  groupLabel: props.groupLabel,
  columnLabel: props.columnLabel,
  valueLabel: props.valueLabel,
  metricLabels: props.metricLabels,
}))

function formatCell(value: unknown) {
  return typeof value === 'number' ? value.toLocaleString() : value
}
</script>

<style scoped>
.data-table-widget {
  height: 100%;
  overflow: hidden;
}
</style>
