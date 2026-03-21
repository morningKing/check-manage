/**
 * DataTableWidget - 聚合数据表格
 */
<template>
  <div class="data-table-widget">
    <el-table :data="data" size="small" stripe max-height="100%" style="width: 100%">
      <el-table-column prop="key" :label="keyLabel" min-width="120" show-overflow-tooltip />
      <el-table-column prop="value" :label="valueLabel" width="120" align="right">
        <template #default="{ row }">
          {{ typeof row.value === 'number' ? row.value.toLocaleString() : row.value }}
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  data: Array<{ key: string; value: number }>
  keyLabel?: string
  valueLabel?: string
}>(), {
  keyLabel: '分组',
  valueLabel: '值',
})
</script>

<style scoped>
.data-table-widget {
  height: 100%;
  overflow: hidden;
}
</style>
