<template>
  <div class="query-page">
    <!-- Top: Editor Area -->
    <div class="editor-area">
      <div class="editor-toolbar">
        <el-select
          v-model="selectedCollection"
          placeholder="选择集合"
          filterable
          style="width: 220px"
          @change="handleCollectionChange"
        >
          <el-option
            v-for="c in collections"
            :key="c.collection"
            :label="`${c.name} (${c.collection})`"
            :value="c.collection"
          />
        </el-select>
        <el-button type="primary" :icon="CaretRight" :loading="executing" @click="executeQuery">
          执行 (Ctrl+Enter)
        </el-button>
        <el-button :icon="Refresh" @click="formatJson">格式化</el-button>
        <el-button
          :icon="Download"
          :disabled="!resultData.length"
          @click="handleExport"
        >
          导出 Excel
        </el-button>
        <div class="toolbar-right">
          <span v-if="resultTotal >= 0" class="result-info">
            {{ resultTotal }} 条结果
            <template v-if="resultTime">({{ resultTime }}ms)</template>
          </span>
        </div>
      </div>
      <div class="editor-body">
        <div class="editor-main">
          <Codemirror
            v-model="queryText"
            :extensions="cmExtensions"
            :style="{ height: '100%' }"
            placeholder="请输入查询语句..."
            @keydown="handleEditorKeydown"
          />
        </div>
        <div class="editor-sidebar">
          <div class="sidebar-section">
            <div class="sidebar-title">字段列表</div>
            <el-input
              v-model="fieldFilter"
              placeholder="搜索字段..."
              clearable
              size="small"
              style="margin-bottom: 8px"
            />
            <el-scrollbar class="field-list-scroll">
              <div
                v-for="f in filteredFields"
                :key="f.fieldName"
                class="field-item"
                :title="`${f.label} (${f.fieldName}) - ${f.controlType}`"
                @click="insertField(f)"
              >
                <span class="field-label">{{ f.label }}</span>
                <span class="field-name">{{ f.fieldName }}</span>
                <el-tag v-if="f.targetCollection" size="small" type="info">
                  {{ f.controlType === 'relation' ? 'M:N' : f.controlType === 'reference' ? 'ref' : 'quote' }}
                </el-tag>
              </div>
              <div v-if="!filteredFields.length" class="field-empty">
                {{ selectedCollection ? '无匹配字段' : '请先选择集合' }}
              </div>
            </el-scrollbar>
          </div>
          <div class="sidebar-section">
            <div class="sidebar-title">语法参考</div>
            <el-scrollbar class="syntax-scroll">
              <div class="syntax-list">
                <div v-for="item in syntaxRef" :key="item.label" class="syntax-item" @click="insertSnippet(item.snippet)">
                  <code>{{ item.label }}</code>
                  <span>{{ item.desc }}</span>
                </div>
              </div>
            </el-scrollbar>
          </div>
        </div>
      </div>
    </div>

    <!-- Bottom: Results Area -->
    <div class="results-area">
      <el-alert v-if="queryError" :title="queryError" type="error" show-icon :closable="false" />
      <div v-if="resultData.length" class="results-table-wrapper">
        <el-table
          :data="resultData"
          stripe
          border
          size="small"
          :max-height="resultTableHeight"
          style="width: 100%"
        >
          <el-table-column type="index" label="#" width="50" fixed />
          <el-table-column
            v-for="col in resultColumns"
            :key="col.key"
            :prop="col.key"
            :label="col.label"
            :min-width="col.isLookup ? 200 : 140"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <template v-if="col.isLookup">
                <span v-if="Array.isArray(row[col.key])">
                  {{ row[col.key].map(formatLookupItem).join('; ') }}
                </span>
                <span v-else-if="row[col.key] && typeof row[col.key] === 'object'">
                  {{ formatLookupItem(row[col.key]) }}
                </span>
                <span v-else>{{ row[col.key] ?? '-' }}</span>
              </template>
              <template v-else>
                {{ formatCellValue(row[col.key]) }}
              </template>
            </template>
          </el-table-column>
        </el-table>
        <div class="results-pagination">
          <el-pagination
            v-model:current-page="currentPage"
            :page-size="pageSize"
            :page-sizes="[50, 100, 200, 500]"
            :total="resultTotal"
            layout="total, sizes, prev, pager, next"
            @size-change="handleSizeChange"
            @current-change="handlePageChange"
          />
        </div>
      </div>
      <el-empty v-else-if="!queryError && executed" description="无匹配数据" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { CaretRight, Refresh, Download } from '@element-plus/icons-vue'
import { Codemirror } from 'vue-codemirror'
import { json, jsonParseLinter } from '@codemirror/lang-json'
import { oneDark } from '@codemirror/theme-one-dark'
import { autocompletion } from '@codemirror/autocomplete'
import { linter } from '@codemirror/lint'
import { get, post } from '@/utils/request'
import * as XLSX from 'xlsx'

interface FieldInfo {
  fieldName: string
  label: string
  controlType: string
  targetCollection?: string
  options?: { label: string; value: string }[]
}

interface CollectionInfo {
  collection: string
  name: string
  fields: FieldInfo[]
}

interface ResultColumn {
  key: string
  label: string
  isLookup?: boolean
}

// State
const collections = ref<CollectionInfo[]>([])
const selectedCollection = ref('')
const queryText = ref(`{
  "collection": "",
  "query": {},
  "lookup": [],
  "sort": {},
  "limit": 200
}`)
const fieldFilter = ref('')
const executing = ref(false)
const executed = ref(false)
const queryError = ref('')
const resultData = ref<any[]>([])
const resultColumns = ref<ResultColumn[]>([])
const resultTotal = ref(-1)
const resultTime = ref(0)
const currentPage = ref(1)
const pageSize = ref(100)
const resultTableHeight = ref(400)

// Build autocomplete source from current collection fields
function fieldCompletions(context: any) {
  const word = context.matchBefore(/[\w\u4e00-\u9fff]+/)
  if (!word) return null

  const col = collections.value.find(c => c.collection === selectedCollection.value)
  if (!col) return null

  const options = col.fields.map(f => ({
    label: f.label,
    detail: f.fieldName,
    apply: `"${f.fieldName}"`,
    type: 'property',
  }))

  // Add operators
  const ops = [
    { label: '$eq', detail: '等于' },
    { label: '$ne', detail: '不等于' },
    { label: '$gt', detail: '大于' },
    { label: '$gte', detail: '大于等于' },
    { label: '$lt', detail: '小于' },
    { label: '$lte', detail: '小于等于' },
    { label: '$in', detail: '在列表中' },
    { label: '$nin', detail: '不在列表中' },
    { label: '$regex', detail: '正则匹配' },
    { label: '$like', detail: '模糊匹配' },
    { label: '$exists', detail: '字段存在' },
    { label: '$and', detail: '逻辑与' },
    { label: '$or', detail: '逻辑或' },
    { label: '$not', detail: '逻辑非' },
    { label: '$lookup', detail: '连表查询' },
  ]
  options.push(...ops.map(o => ({
    label: o.label,
    detail: o.detail,
    apply: `"${o.label}"`,
    type: 'keyword',
  })))

  return {
    from: word.from,
    options,
    filter: true,
  }
}

const cmExtensions = computed(() => [
  json(),
  oneDark,
  linter(jsonParseLinter()),
  autocompletion({
    override: [fieldCompletions],
    activateOnTyping: true,
  }),
])

// Current collection fields
const currentFields = computed<FieldInfo[]>(() => {
  const col = collections.value.find(c => c.collection === selectedCollection.value)
  return col?.fields || []
})

const filteredFields = computed(() => {
  const kw = fieldFilter.value.trim().toLowerCase()
  if (!kw) return currentFields.value
  return currentFields.value.filter(
    f => f.label.toLowerCase().includes(kw) || f.fieldName.toLowerCase().includes(kw)
  )
})

// Syntax reference
const syntaxRef = [
  { label: '$regex', desc: '正则匹配', snippet: '{"$regex": ""}' },
  { label: '$like', desc: '模糊匹配', snippet: '{"$like": ""}' },
  { label: '$gt / $gte', desc: '大于', snippet: '{"$gt": 0}' },
  { label: '$lt / $lte', desc: '小于', snippet: '{"$lt": 0}' },
  { label: '$in', desc: '在列表中', snippet: '{"$in": ["a", "b"]}' },
  { label: '$ne', desc: '不等于', snippet: '{"$ne": ""}' },
  { label: '$exists', desc: '字段存在', snippet: '{"$exists": true}' },
  { label: '$or', desc: '逻辑或', snippet: '"$or": [{}, {}]' },
  { label: '$and', desc: '逻辑与', snippet: '"$and": [{}, {}]' },
  { label: '$lookup', desc: '连表查询', snippet: '{"from": "", "localField": "", "as": ""}' },
]

// Load collections on mount
onMounted(async () => {
  try {
    collections.value = await get<CollectionInfo[]>('/query/collections')
  } catch {
    ElMessage.error('加载集合列表失败')
  }
  calcTableHeight()
  window.addEventListener('resize', calcTableHeight)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', calcTableHeight)
})

function calcTableHeight() {
  // Approximate: page height minus editor and toolbar areas
  resultTableHeight.value = Math.max(window.innerHeight - 520, 200)
}

function handleCollectionChange(col: string) {
  // Auto-fill collection in the query
  try {
    const obj = JSON.parse(queryText.value)
    obj.collection = col
    queryText.value = JSON.stringify(obj, null, 2)
  } catch {
    // If query text is not valid JSON, just set a template
    queryText.value = JSON.stringify({
      collection: col,
      query: {},
      lookup: [],
      sort: {},
      limit: 200,
    }, null, 2)
  }
}

function formatJson() {
  try {
    const obj = JSON.parse(queryText.value)
    queryText.value = JSON.stringify(obj, null, 2)
  } catch (e: any) {
    ElMessage.warning('JSON 格式错误: ' + e.message)
  }
}

function insertField(f: FieldInfo) {
  // Attempt to parse and insert intelligently
  try {
    const obj = JSON.parse(queryText.value)
    if (obj.query && typeof obj.query === 'object') {
      obj.query[f.label] = ''
      queryText.value = JSON.stringify(obj, null, 2)
      return
    }
  } catch {
    // fallthrough
  }
  // Fallback: append to text
  ElMessage.info(`已复制: "${f.label}" → ${f.fieldName}`)
}

function insertSnippet(snippet: string) {
  // Copy snippet for user to paste
  navigator.clipboard.writeText(snippet).then(() => {
    ElMessage.success('已复制到剪贴板')
  }).catch(() => {
    ElMessage.info(snippet)
  })
}

function handleEditorKeydown(e: KeyboardEvent) {
  if (e.ctrlKey && e.key === 'Enter') {
    e.preventDefault()
    executeQuery()
  }
}

async function executeQuery() {
  queryError.value = ''
  executed.value = false

  let body: any
  try {
    body = JSON.parse(queryText.value)
  } catch (e: any) {
    queryError.value = 'JSON 格式错误: ' + e.message
    return
  }

  if (!body.collection) {
    queryError.value = '请指定 collection 字段'
    return
  }

  // Set pagination
  body.skip = (currentPage.value - 1) * pageSize.value
  body.limit = pageSize.value

  // Also update the collection selector
  if (body.collection !== selectedCollection.value) {
    selectedCollection.value = body.collection
  }

  executing.value = true
  const startTime = Date.now()

  try {
    const res = await post<any>('/query/execute', body)
    resultTime.value = Date.now() - startTime
    resultData.value = res.data || []
    resultTotal.value = res.total || 0
    resultColumns.value = res.columns || []
    executed.value = true

    if (resultData.value.length === 0 && resultTotal.value > 0) {
      currentPage.value = 1
      // Re-execute with page 1
      body.skip = 0
      const res2 = await post<any>('/query/execute', body)
      resultData.value = res2.data || []
      resultColumns.value = res2.columns || []
    }
  } catch (e: any) {
    const msg = e?.response?.data?.error || e.message || '查询执行失败'
    queryError.value = msg
    resultData.value = []
    resultTotal.value = -1
  } finally {
    executing.value = false
  }
}

function handlePageChange(page: number) {
  currentPage.value = page
  executeQuery()
}

function handleSizeChange(size: number) {
  pageSize.value = size
  currentPage.value = 1
  executeQuery()
}

function formatCellValue(val: any): string {
  if (val === null || val === undefined) return '-'
  if (Array.isArray(val)) return val.join(', ')
  if (typeof val === 'object') return JSON.stringify(val)
  if (typeof val === 'boolean') return val ? '是' : '否'
  return String(val)
}

function formatLookupItem(item: any): string {
  if (!item || typeof item !== 'object') return String(item ?? '-')
  // Try to find a display field (first string value that's not _id)
  const keys = Object.keys(item).filter(k => k !== '_id')
  for (const k of keys) {
    const v = item[k]
    if (typeof v === 'string' && v.length > 0) return v
  }
  return item._id || JSON.stringify(item)
}

function handleExport() {
  if (!resultData.value.length) return

  const headers = resultColumns.value.map(c => c.label)
  const keys = resultColumns.value.map(c => c.key)

  const rows = resultData.value.map(row =>
    keys.map(k => {
      const val = row[k]
      if (val === null || val === undefined) return ''
      if (Array.isArray(val)) {
        if (val.length > 0 && typeof val[0] === 'object') {
          return val.map(formatLookupItem).join('; ')
        }
        return val.join(', ')
      }
      if (typeof val === 'object') return formatLookupItem(val)
      return val
    })
  )

  const wsData = [headers, ...rows]
  const ws = XLSX.utils.aoa_to_sheet(wsData)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '查询结果')

  const colName = collections.value.find(c => c.collection === selectedCollection.value)?.name || selectedCollection.value
  XLSX.writeFile(wb, `查询结果_${colName}_${new Date().toISOString().slice(0, 10)}.xlsx`)
  ElMessage.success('导出成功')
}
</script>

<style scoped lang="scss">
.query-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.editor-area {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-bottom: 2px solid var(--el-border-color, #dcdfe6);
}

.editor-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: var(--el-bg-color, #fff);
  border-bottom: 1px solid var(--el-border-color-lighter, #e4e7ed);

  .toolbar-right {
    margin-left: auto;
  }

  .result-info {
    color: var(--el-text-color-secondary, #909399);
    font-size: 13px;
  }
}

.editor-body {
  display: flex;
  height: 260px;
  min-height: 160px;
}

.editor-main {
  flex: 1;
  overflow: hidden;

  :deep(.cm-editor) {
    height: 100%;
    font-size: 13px;
  }

  :deep(.cm-scroller) {
    font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  }
}

.editor-sidebar {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--el-border-color-lighter, #e4e7ed);
  background: var(--el-fill-color-lighter, #fafafa);
  overflow: hidden;
}

.sidebar-section {
  display: flex;
  flex-direction: column;
  padding: 8px 10px;
  overflow: hidden;

  &:first-child {
    flex: 1;
    min-height: 0;
    border-bottom: 1px solid var(--el-border-color-extra-light, #f2f6fc);
  }

  &:last-child {
    flex-shrink: 0;
    max-height: 140px;
  }
}

.sidebar-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary, #909399);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.field-list-scroll {
  flex: 1;
  min-height: 0;
}

.field-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  cursor: pointer;
  border-radius: 3px;
  font-size: 12px;
  transition: background 0.15s;

  &:hover {
    background: var(--el-fill-color, #f5f7fa);
  }

  .field-label {
    color: var(--el-text-color-primary, #303133);
    white-space: nowrap;
  }

  .field-name {
    color: var(--el-text-color-placeholder, #a8abb2);
    font-family: monospace;
    font-size: 11px;
    margin-left: auto;
    white-space: nowrap;
  }
}

.field-empty {
  text-align: center;
  color: var(--el-text-color-placeholder, #c0c4cc);
  font-size: 12px;
  padding: 16px 0;
}

.syntax-scroll {
  flex: 1;
  min-height: 0;
}

.syntax-item {
  display: flex;
  gap: 8px;
  padding: 3px 4px;
  cursor: pointer;
  font-size: 11px;
  border-radius: 3px;

  &:hover {
    background: var(--el-fill-color, #f5f7fa);
  }

  code {
    color: #409eff;
    font-family: monospace;
    white-space: nowrap;
  }

  span {
    color: var(--el-text-color-secondary, #909399);
  }
}

/* Results */
.results-area {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0 16px 12px;
}

.results-table-wrapper {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding-top: 8px;
}

.results-pagination {
  display: flex;
  justify-content: flex-end;
  padding-top: 8px;
}
</style>
