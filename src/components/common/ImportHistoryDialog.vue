<template>
  <el-dialog v-model="visible" title="导入历史" width="900px" :close-on-click-modal="false" destroy-on-close>
    <el-table v-loading="loading" :data="runs" stripe style="width: 100%">
      <el-table-column prop="fileName" label="文件名" min-width="160" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'success' ? 'success' : 'warning'" size="small">
            {{ row.status === 'success' ? '成功' : '部分失败' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="成功 / 失败" width="140">
        <template #default="{ row }">
          {{ row.successCount }} / {{ row.failedCount }}
        </template>
      </el-table-column>
      <el-table-column prop="operator" label="操作人" width="120" />
      <el-table-column label="时间" width="180">
        <template #default="{ row }">{{ formatDateTime(row.createdAt) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link @click="showDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > pageSize"
      v-model:current-page="currentPage"
      :page-size="pageSize"
      :total="total"
      layout="prev, pager, next"
      style="margin-top: 12px; justify-content: flex-end"
      @current-change="fetchRuns"
    />

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="detailVisible" title="导入详情" width="700px" :close-on-click-modal="false">
    <div v-if="detailRun" class="import-history-detail-summary">
      <p>{{ detailRun.fileName }} — 成功 {{ detailRun.successCount }} 条，失败 {{ detailRun.failedCount }} 条</p>
    </div>
    <el-table v-loading="detailLoading" :data="detailFailures" size="small" max-height="360">
      <el-table-column label="原始数据" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ JSON.stringify(row.originalRecord) }}</template>
      </el-table-column>
      <el-table-column prop="reason" label="失败原因" min-width="160" show-overflow-tooltip />
    </el-table>
    <template #footer>
      <el-button v-if="detailFailures.length > 0" @click="handleExportDetailFailures">导出失败清单</el-button>
      <el-button
        v-if="detailFailures.length > 0"
        type="primary"
        :loading="retrying"
        @click="handleRetryDetailFailures"
      >
        重试失败记录
      </el-button>
      <el-button type="primary" @click="detailVisible = false">确定</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { FieldConfig } from '@/types'
import {
  listImportRuns, getImportRunDetail, syncImportRunRetryResult,
  type ImportRunSummary, type ImportRunFailureRow,
} from '@/api/importRuns'
import {
  retryImportFailures, diffRetryResult, type ImportFailure, type ImportPageResult,
} from '@/utils/importPageRecords'
import { exportImportFailures } from '@/utils/excel'
import { post } from '@/utils/request'

const props = defineProps<{
  modelValue: boolean
  pageId: string
  collection: string
  fields: FieldConfig[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const loading = ref(false)
const runs = ref<ImportRunSummary[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = 20

async function fetchRuns(): Promise<void> {
  loading.value = true
  try {
    const resp = await listImportRuns(props.pageId, props.collection, pageSize, (currentPage.value - 1) * pageSize)
    runs.value = resp.runs
    total.value = resp.total
  } catch {
    ElMessage.error('加载导入历史失败')
  } finally {
    loading.value = false
  }
}

watch(visible, (val) => {
  if (val) {
    currentPage.value = 1
    fetchRuns()
  }
})

const detailVisible = ref(false)
const detailLoading = ref(false)
const detailRun = ref<ImportRunSummary | null>(null)
const detailFailures = ref<ImportRunFailureRow[]>([])
const retrying = ref(false)

async function showDetail(row: ImportRunSummary): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    const resp = await getImportRunDetail(row.id)
    detailRun.value = resp.run
    detailFailures.value = resp.failures
  } catch {
    ElMessage.error('加载导入详情失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

function toImportFailures(rows: ImportRunFailureRow[]): ImportFailure[] {
  return rows.map((r) => ({ originalRecord: r.originalRecord, payload: r.payload, reason: r.reason }))
}

function handleExportDetailFailures(): void {
  if (!detailRun.value) return
  const pad = (n: number) => String(n).padStart(2, '0')
  const now = new Date()
  const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  const baseName = detailRun.value.fileName.replace(/\.[^.]+$/, '')
  exportImportFailures(toImportFailures(detailFailures.value), props.fields, `导入失败记录_${baseName}_${ts}`)
}

async function handleRetryDetailFailures(): Promise<void> {
  if (!detailRun.value) return
  const run = detailRun.value
  const before: ImportPageResult = {
    success: run.successCount, created: run.createdCount, updated: run.updatedCount,
    failed: run.failedCount, failures: toImportFailures(detailFailures.value),
  }

  retrying.value = true
  try {
    let retryResult: Awaited<ReturnType<typeof retryImportFailures>>
    try {
      retryResult = await retryImportFailures(post, props.collection, before.failures, () => {})
    } catch {
      ElMessage.error('重试失败，请稍后再试')
      return
    }

    const after: ImportPageResult = {
      success: before.success + retryResult.success,
      created: before.created + retryResult.created,
      updated: before.updated + retryResult.updated,
      failed: retryResult.failed,
      failures: retryResult.failures,
    }
    const diff = diffRetryResult(before, after)

    if (diff.resolvedRecordIds.length === 0) {
      ElMessage.warning('本次重试没有记录成功')
      return
    }

    // 重试本身已经成功，无论后面的历史同步是否成功都要反映到本地状态
    const updatedRun: ImportRunSummary = {
      ...run,
      successCount: after.success,
      createdCount: after.created,
      updatedCount: after.updated,
      failedCount: after.failed,
      status: after.failed > 0 ? 'partial' : 'success',
    }
    detailRun.value = updatedRun
    detailFailures.value = detailFailures.value.filter(
      (f) => !diff.resolvedRecordIds.includes(f.recordId),
    )
    const idx = runs.value.findIndex((r) => r.id === run.id)
    if (idx !== -1) runs.value[idx] = { ...runs.value[idx], ...updatedRun }

    try {
      const synced = await syncImportRunRetryResult(run.id, diff)
      detailRun.value = { ...detailRun.value, ...synced } as ImportRunSummary
      if (idx !== -1) runs.value[idx] = { ...runs.value[idx], ...synced } as ImportRunSummary
      ElMessage.success(`重试完成：解决 ${diff.resolvedRecordIds.length} 条，仍失败 ${after.failed} 条`)
    } catch (e) {
      console.warn('导入历史同步失败（本次重试结果不受影响）', e)
      ElMessage.warning(
        `重试完成：解决 ${diff.resolvedRecordIds.length} 条，仍失败 ${after.failed} 条（导入历史同步失败，可能显示旧数字）`,
      )
    }
  } finally {
    retrying.value = false
  }
}

function formatDateTime(iso: string): string {
  if (!iso) return ''
  return iso.replace('T', ' ').replace(/\.\d+Z?$/, '').slice(0, 19)
}
</script>

<style scoped>
.import-history-detail-summary {
  margin-bottom: 12px;
  color: #606266;
}
</style>
