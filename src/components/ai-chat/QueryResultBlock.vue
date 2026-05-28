<script setup lang="ts">
import { computed } from 'vue'
import { ElTable, ElTableColumn, ElButton, ElIcon } from 'element-plus'
import { Download } from '@element-plus/icons-vue'
import * as XLSX from 'xlsx'

interface Col { key: string; label: string; isLookup?: boolean }
interface QueryResult {
  mode: string
  collection: string
  total: number
  columns?: Col[]
  rows?: Record<string, any>[]
  file?: string
  capped?: boolean
}
const props = defineProps<{ result: QueryResult; downloadUrl: (path: string) => string }>()

const isTable = computed(() => props.result?.mode === 'table')
const columns = computed<Col[]>(() => props.result?.columns ?? [])
const rows = computed<Record<string, any>[]>(() => props.result?.rows ?? [])

function cell(row: Record<string, any>, col: Col): string {
  const v = row[col.key]
  if (v == null) return ''
  if (Array.isArray(v)) return v.length ? `${v.length} 项` : ''
  if (typeof v === 'object') return v.name ?? v.title ?? JSON.stringify(v)
  if (typeof v === 'boolean') return v ? '是' : '否'
  return String(v)
}

function downloadXlsx() {
  const data = rows.value.map((r) => {
    const o: Record<string, any> = {}
    for (const c of columns.value) o[c.label] = cell(r, c)
    return o
  })
  const ws = XLSX.utils.json_to_sheet(data)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, 'data')
  XLSX.writeFile(wb, `${props.result.collection || 'query'}.xlsx`)
}
</script>

<template>
  <div class="query-result">
    <template v-if="isTable">
      <div class="query-result__bar">
        <span class="query-result__count">共 {{ result.total }} 条</span>
        <ElButton size="small" :icon="Download" @click="downloadXlsx">下载 Excel</ElButton>
      </div>
      <ElTable :data="rows" size="small" border max-height="360" class="query-result__table">
        <ElTableColumn
          v-for="c in columns" :key="c.key" :prop="c.key" :label="c.label"
          show-overflow-tooltip min-width="120"
        >
          <template #default="{ row }">{{ cell(row, c) }}</template>
        </ElTableColumn>
      </ElTable>
    </template>
    <a
      v-else-if="result.file"
      class="query-result__file"
      :href="downloadUrl(result.file)" target="_blank" rel="noopener"
    >
      <ElIcon><Download /></ElIcon>
      <span>{{ result.collection }} 查询结果.xlsx（共 {{ result.total }} 条{{ result.capped ? '，已截断至 5 万' : '' }}）</span>
    </a>
  </div>
</template>

<style scoped lang="scss">
.query-result { margin: 8px 0; }
.query-result__bar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.query-result__count { font-size: 13px; color: var(--el-text-color-secondary); }
.query-result__file {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 12px; border: 1px solid var(--el-border-color); border-radius: 8px;
  text-decoration: none; color: var(--el-color-primary); font-size: 14px;
  &:hover { background: var(--el-fill-color-light); }
}
</style>
