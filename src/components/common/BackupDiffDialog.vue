/**
 * 备份数据对比对话框
 *
 * 职责：
 * - 选择基准/对比数据源（当前数据 或 历史备份）
 * - 调用后端 diff 接口获取差异
 * - 展示新增、删除、修改记录及字段级变更
 * - 支持导出对比报告为 Excel
 */
<template>
  <el-dialog
    v-model="visible"
    title="数据对比"
    width="90%"
    top="5vh"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <!-- 数据源选择 -->
    <div class="diff-source-bar">
      <div class="source-item">
        <span class="source-label">基准数据</span>
        <el-select
          v-model="baseSource"
          placeholder="选择基准"
          style="width: 280px"
          :disabled="diffLoading"
        >
          <el-option label="当前数据" value="current" />
          <el-option-group label="版本快照">
            <el-option
              v-for="v in versionList"
              :key="v.id"
              :label="v.name + (v.status === 'merged' ? ' (已合并)' : '')"
              :value="v.id"
              :disabled="v.status === 'merged'"
            />
          </el-option-group>
          <el-option-group label="历史备份">
            <el-option
              v-for="b in backupList"
              :key="b.id"
              :label="b.name + (b.note ? ` (${b.note})` : '')"
              :value="b.id"
            />
          </el-option-group>
        </el-select>
      </div>
      <el-icon class="source-arrow"><Right /></el-icon>
      <div class="source-item">
        <span class="source-label">对比数据</span>
        <el-select
          v-model="targetSource"
          placeholder="选择对比"
          style="width: 280px"
          :disabled="diffLoading"
        >
          <el-option label="当前数据" value="current" />
          <el-option-group label="版本快照">
            <el-option
              v-for="v in versionList"
              :key="v.id"
              :label="v.name + (v.status === 'merged' ? ' (已合并)' : '')"
              :value="v.id"
              :disabled="v.status === 'merged'"
            />
          </el-option-group>
          <el-option-group label="历史备份">
            <el-option
              v-for="b in backupList"
              :key="b.id"
              :label="b.name + (b.note ? ` (${b.note})` : '')"
              :value="b.id"
            />
          </el-option-group>
        </el-select>
      </div>
      <el-button
        type="primary"
        :loading="diffLoading"
        :disabled="!canCompare"
        @click="handleCompare"
      >
        开始对比
      </el-button>
      <el-button
        v-if="diffResult"
        type="success"
        @click="handleExportReport"
      >
        <el-icon><Download /></el-icon>
        导出报告
      </el-button>
    </div>

    <!-- 结果摘要 -->
    <div v-if="diffResult" class="diff-summary">
      <el-tag type="success">新增 {{ diffResult.added.length }} 条</el-tag>
      <el-tag type="danger">删除 {{ diffResult.removed.length }} 条</el-tag>
      <el-tag type="warning">修改 {{ diffResult.modified.length }} 条</el-tag>
      <el-tag type="info">未变 {{ diffResult.unchangedCount }} 条</el-tag>
    </div>

    <!-- 无差异 -->
    <el-empty
      v-if="diffResult && !hasChanges"
      description="两个数据源完全一致，无差异"
    />

    <!-- 差异详情 -->
    <div v-if="diffResult && hasChanges" class="diff-detail">
      <!-- 新增记录 -->
      <div v-if="diffResult.added.length > 0" class="diff-section">
        <h4 class="section-title section-added">
          <el-icon><CirclePlusFilled /></el-icon>
          新增记录（{{ diffResult.added.length }}）
        </h4>
        <el-table :data="diffResult.added" border stripe size="small" max-height="300">
          <el-table-column
            v-for="col in displayColumns"
            :key="col.fieldName"
            :prop="col.fieldName"
            :label="col.label"
            :min-width="120"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ formatValue(row[col.fieldName]) }}
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 删除记录 -->
      <div v-if="diffResult.removed.length > 0" class="diff-section">
        <h4 class="section-title section-removed">
          <el-icon><RemoveFilled /></el-icon>
          删除记录（{{ diffResult.removed.length }}）
        </h4>
        <el-table :data="diffResult.removed" border stripe size="small" max-height="300">
          <el-table-column
            v-for="col in displayColumns"
            :key="col.fieldName"
            :prop="col.fieldName"
            :label="col.label"
            :min-width="120"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ formatValue(row[col.fieldName]) }}
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 修改记录 -->
      <div v-if="diffResult.modified.length > 0" class="diff-section">
        <h4 class="section-title section-modified">
          <el-icon><EditPen /></el-icon>
          修改记录（{{ diffResult.modified.length }}）
        </h4>
        <el-table :data="diffResult.modified" border stripe size="small" max-height="400" row-key="id">
          <el-table-column prop="id" label="记录 ID" width="160" show-overflow-tooltip>
            <template #default="{ row }">
              {{ getRecordDisplayId(row) }}
            </template>
          </el-table-column>
          <el-table-column label="变更字段" min-width="160">
            <template #default="{ row }">
              {{ row.fields.map((f: any) => getFieldLabel(f.fieldName)).join('、') }}
            </template>
          </el-table-column>
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="field-changes">
                <div v-for="f in row.fields" :key="f.fieldName" class="field-change-row">
                  <span class="change-label">{{ getFieldLabel(f.fieldName) }}</span>
                  <span class="change-old">{{ formatValue(f.oldValue) }}</span>
                  <el-icon class="change-arrow"><Right /></el-icon>
                  <span class="change-new">{{ formatValue(f.newValue) }}</span>
                </div>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- 初始空状态 -->
    <el-empty
      v-if="!diffResult && !diffLoading"
      description="选择基准和对比数据源后点击「开始对比」"
    />

    <template #footer>
      <div class="dialog-footer-bar">
        <el-button
          v-if="mergeableVersionId"
          type="primary"
          @click="handleMerge"
        >
          从此版本合并
        </el-button>
        <el-button @click="visible = false">关闭</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Right, Download, CirclePlusFilled, RemoveFilled, EditPen } from '@element-plus/icons-vue'
import * as XLSX from 'xlsx'
import { getBackups, diffBackupCollection } from '@/api/backup'
import { getVersions } from '@/api/version'
import type { Backup, DiffResult, FieldConfig } from '@/types'
import type { CollectionVersion } from '@/types'

// ==================== Props & Emits ====================

interface Props {
  modelValue: boolean
  collection: string
  pageName: string
  baseSource?: string    // 可选的默认基准数据源
  targetSource?: string  // 可选的默认对比数据源
}

const props = withDefaults(defineProps<Props>(), {
  baseSource: 'current',
  targetSource: '',
})
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'merge', versionId: string): void
}>()

// ==================== State ====================

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const backupList = ref<Backup[]>([])
const versionList = ref<CollectionVersion[]>([])
const baseSource = ref('current')
const targetSource = ref('')
const diffLoading = ref(false)
const diffResult = ref<DiffResult | null>(null)

// ==================== Computed ====================

const canCompare = computed(() => {
  return baseSource.value && targetSource.value && baseSource.value !== targetSource.value
})

const hasChanges = computed(() => {
  if (!diffResult.value) return false
  return (
    diffResult.value.added.length > 0 ||
    diffResult.value.removed.length > 0 ||
    diffResult.value.modified.length > 0
  )
})

const mergeableVersionId = computed(() => {
  // Show merge button when one side is a version (not 'current' and not a backup)
  // Versions are from versionList; backups are from backupList
  const versionIds = new Set(versionList.value.map(v => v.id))
  if (targetSource.value && targetSource.value !== 'current' && versionIds.has(targetSource.value)) {
    return targetSource.value
  }
  if (baseSource.value && baseSource.value !== 'current' && versionIds.has(baseSource.value)) {
    return baseSource.value
  }
  return null
})

const displayColumns = computed(() => {
  if (!diffResult.value?.fields) return []
  return diffResult.value.fields
    .filter((f: FieldConfig) => !f.hidden)
    .sort((a: FieldConfig, b: FieldConfig) => a.order - b.order)
    .slice(0, 8)
})

const fieldLabelMap = computed(() => {
  const map: Record<string, string> = {}
  if (diffResult.value?.fields) {
    for (const f of diffResult.value.fields) {
      map[f.fieldName] = f.label
    }
  }
  return map
})

// ==================== Methods ====================

function getFieldLabel(fieldName: string): string {
  return fieldLabelMap.value[fieldName] || fieldName
}

function getRecordDisplayId(row: any): string {
  if (!diffResult.value?.fields) return row.id
  for (const f of diffResult.value.fields) {
    if (f.isPrimaryKey || f.controlType === 'autoSequence') {
      const val = row.record?.[f.fieldName]
      if (val) return String(val)
    }
  }
  const firstField = diffResult.value.fields
    .filter((f: FieldConfig) => ['text', 'textarea'].includes(f.controlType))
    .sort((a: FieldConfig, b: FieldConfig) => a.order - b.order)[0]
  if (firstField) {
    const val = row.record?.[firstField.fieldName]
    if (val) return String(val)
  }
  return row.id
}

function formatValue(val: any): string {
  if (val === null || val === undefined) return '-'
  if (Array.isArray(val)) return val.join('、')
  return String(val)
}

async function loadBackups() {
  try {
    backupList.value = await getBackups()
  } catch {
    backupList.value = []
  }
  // 同时加载版本列表
  try {
    versionList.value = await getVersions(props.collection)
  } catch {
    versionList.value = []
  }
}

async function handleCompare() {
  if (!canCompare.value) return
  diffLoading.value = true
  diffResult.value = null
  try {
    diffResult.value = await diffBackupCollection(
      props.collection,
      baseSource.value,
      targetSource.value
    )
  } catch (e: any) {
    const msg = e?.response?.data?.error || '对比失败'
    ElMessage.error(msg)
  } finally {
    diffLoading.value = false
  }
}

function handleExportReport() {
  if (!diffResult.value) return

  const fields = diffResult.value.fields || []
  const sortedFields = [...fields]
    .filter((f: FieldConfig) => !f.hidden)
    .sort((a: FieldConfig, b: FieldConfig) => a.order - b.order)
  const headers = ['变更类型', ...sortedFields.map((f: FieldConfig) => f.label)]

  const rows: string[][] = []

  for (const rec of diffResult.value.added) {
    rows.push(['新增', ...sortedFields.map((f: FieldConfig) => formatValue(rec[f.fieldName]))])
  }

  for (const rec of diffResult.value.removed) {
    rows.push(['删除', ...sortedFields.map((f: FieldConfig) => formatValue(rec[f.fieldName]))])
  }

  for (const item of diffResult.value.modified) {
    const changedSet = new Set(item.fields.map((f) => f.fieldName))
    const vals = sortedFields.map((f: FieldConfig) => {
      if (changedSet.has(f.fieldName)) {
        const cf = item.fields.find((c) => c.fieldName === f.fieldName)!
        return `${formatValue(cf.oldValue)} -> ${formatValue(cf.newValue)}`
      }
      return formatValue(item.record[f.fieldName])
    })
    rows.push(['修改', ...vals])
  }

  const wsData = [headers, ...rows]
  const ws = XLSX.utils.aoa_to_sheet(wsData)
  ws['!cols'] = headers.map((h) => ({ wch: Math.max(h.length * 2 + 4, 14) }))

  const baseLabel = baseSource.value === 'current' ? '当前数据'
    : (backupList.value.find((b) => b.id === baseSource.value)?.name || baseSource.value)
  const targetLabel = targetSource.value === 'current' ? '当前数据'
    : (backupList.value.find((b) => b.id === targetSource.value)?.name || targetSource.value)

  // 摘要 sheet
  const summaryRows = [
    ['对比报告'],
    ['页面', props.pageName],
    ['基准数据', baseLabel],
    ['对比数据', targetLabel],
    [],
    ['新增', String(diffResult.value.added.length)],
    ['删除', String(diffResult.value.removed.length)],
    ['修改', String(diffResult.value.modified.length)],
    ['未变', String(diffResult.value.unchangedCount)],
  ]
  const summaryWs = XLSX.utils.aoa_to_sheet(summaryRows)
  summaryWs['!cols'] = [{ wch: 14 }, { wch: 40 }]

  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, summaryWs, '摘要')
  XLSX.utils.book_append_sheet(wb, ws, '变更明细')

  XLSX.writeFile(wb, `${props.pageName}-对比报告.xlsx`)
  ElMessage.success('报告已导出')
}

function handleMerge() {
  if (mergeableVersionId.value) {
    emit('merge', mergeableVersionId.value)
  }
}

// ==================== Watch ====================

watch(visible, (v) => {
  if (v) {
    loadBackups()
    diffResult.value = null
    // 使用 props 传入的值或默认值
    baseSource.value = props.baseSource || 'current'
    targetSource.value = props.targetSource || ''
  }
})
</script>

<style scoped lang="scss">
.diff-source-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;

  .source-item {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .source-label {
    font-size: 13px;
    color: #606266;
    white-space: nowrap;
  }

  .source-arrow {
    font-size: 18px;
    color: #909399;
  }
}

.diff-summary {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.diff-detail {
  max-height: 60vh;
  overflow-y: auto;
}

.diff-section {
  margin-bottom: 20px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 8px 0;

  &.section-added { color: #67c23a; }
  &.section-removed { color: #f56c6c; }
  &.section-modified { color: #e6a23c; }
}

.field-changes {
  padding: 8px 16px;
}

.field-change-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 0;
  font-size: 13px;

  & + & {
    border-top: 1px dashed #ebeef5;
  }

  .change-label {
    font-weight: 500;
    color: #303133;
    min-width: 80px;
  }

  .change-old {
    color: #f56c6c;
    text-decoration: line-through;
    max-width: 300px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .change-arrow {
    color: #909399;
    flex-shrink: 0;
  }

  .change-new {
    color: #67c23a;
    max-width: 300px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.dialog-footer-bar {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>
