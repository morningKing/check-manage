/**
 * 操作日志管理页面
 *
 * 职责：
 * - 查看所有操作日志
 * - 按时间/操作类型/目标类型/操作人筛选
 * - 导出日志为Excel
 * - 删除单条日志（管理员权限）
 * - 按批次分组展示，支持折叠/展开
 *
 * 仅管理员可访问
 */
<template>
  <div class="operation-log">
    <el-card>
      <template #header>
        <div class="card-header">
          <h2>操作日志</h2>
        </div>
      </template>

      <!-- 筛选栏 -->
      <div class="filter-bar">
        <el-date-picker
          v-model="dateRange"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          format="YYYY-MM-DD HH:mm:ss"
          value-format="YYYY-MM-DDTHH:mm:ss.000Z"
          style="width: 380px"
        />
        <el-select
          v-model="filterAction"
          placeholder="操作类型"
          clearable
          style="width: 120px"
        >
          <el-option
            v-for="opt in ACTION_OPTIONS"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-select
          v-model="filterTargetType"
          placeholder="目标类型"
          clearable
          style="width: 140px"
        >
          <el-option
            v-for="opt in TARGET_TYPE_OPTIONS"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-input
          v-model="filterOperatorName"
          placeholder="操作人"
          clearable
          style="width: 150px"
          @keyup.enter="handleSearch"
        />
        <el-button type="primary" @click="handleSearch">
          <el-icon><Search /></el-icon>
          搜索
        </el-button>
        <el-button @click="handleReset">重置</el-button>
        <el-button type="success" @click="handleExport" :loading="exportLoading">
          <el-icon><Download /></el-icon>
          导出
        </el-button>
      </div>

      <!-- 数据表格 -->
      <el-table
        :data="displayData"
        v-loading="loading"
        border
        row-key="id"
        :tree-props="{ children: 'children' }"
        :row-class-name="getRowClassName"
        default-expand-all
        style="width: 100%; margin-top: 16px"
      >
        <el-table-column prop="description" label="操作描述" min-width="250" show-overflow-tooltip>
          <template #default="{ row }">
            <template v-if="row.batchCount">
              <span class="batch-label">
                <el-icon class="batch-icon"><Folder /></el-icon>
                {{ row.description }}
                <el-tag size="small" type="info" round class="batch-count">
                  {{ row.batchCount }} 条
                </el-tag>
              </span>
            </template>
            <template v-else>
              {{ row.description }}
            </template>
          </template>
        </el-table-column>
        <el-table-column prop="action" label="操作类型" width="100" align="center">
          <template #default="{ row }">
            <template v-if="!row.batchCount">
              <el-tag
                :type="ACTION_TAG_TYPES[row.action as OperationAction] || 'info'"
                size="small"
              >
                {{ ACTION_LABELS[row.action as OperationAction] || row.action }}
              </el-tag>
            </template>
          </template>
        </el-table-column>
        <el-table-column prop="targetType" label="目标类型" width="120" align="center">
          <template #default="{ row }">
            <template v-if="!row.batchCount">
              <el-tag type="info" size="small" effect="plain">
                {{ TARGET_TYPE_LABELS[row.targetType as OperationTargetType] || row.targetType }}
              </el-tag>
            </template>
          </template>
        </el-table-column>
        <el-table-column prop="operatorName" label="操作人" width="120" />
        <el-table-column prop="operatorRole" label="角色" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="getRoleTagType(row.operatorRole)" size="small">
              {{ ROLE_LABELS[row.operatorRole as UserRole] || row.operatorRole }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="branchName" label="分支" width="120" align="center">
          <template #default="{ row }">
            <template v-if="!row.batchCount">
              <el-tag v-if="row.branchName && row.branchName !== '主分支'" type="warning" size="small">
                {{ row.branchName }}
              </el-tag>
              <span v-else class="main-branch">主分支</span>
            </template>
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="操作时间" width="180">
          <template #default="{ row }">
            {{ formatDate(row.createdAt) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="!row.batchCount"
              type="danger"
              link
              size="small"
              @click="handleDeleteConfirm(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>

    <!-- 删除确认 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      message="确定要删除这条操作日志吗？"
      type="danger"
      confirm-text="删除"
      @confirm="handleDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, Download, Folder } from '@element-plus/icons-vue'
import { getOperationLogs, deleteOperationLog, exportOperationLogs } from '@/api/operationLog'
import { ConfirmDialog } from '@/components/common'
import {
  ACTION_LABELS,
  ACTION_TAG_TYPES,
  ACTION_OPTIONS,
  TARGET_TYPE_LABELS,
  TARGET_TYPE_OPTIONS,
  ROLE_LABELS,
} from '@/types'
import type { OperationLog, OperationLogRow, OperationAction, OperationTargetType, UserRole } from '@/types'

// ==================== State ====================

const loading = ref(false)
const exportLoading = ref(false)
const logList = ref<OperationLog[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

const dateRange = ref<[string, string] | null>(null)
const filterAction = ref('')
const filterTargetType = ref('')
const filterOperatorName = ref('')

const deleteDialogVisible = ref(false)
const deleteTarget = ref<OperationLog | null>(null)

// ==================== 计算属性 ====================

/**
 * 按 batchId 分组后的展示数据
 * 同一 batchId 的日志聚合为一个父行 + children 子行
 * 无 batchId 的日志保持独立行
 */
const displayData = computed<OperationLogRow[]>(() => {
  const rows: OperationLogRow[] = []
  const batchMap = new Map<string, OperationLogRow>()

  for (const log of logList.value) {
    if (!log.batchId) {
      rows.push({ ...log })
      continue
    }

    let parent = batchMap.get(log.batchId)
    if (!parent) {
      parent = {
        id: `batch-${log.batchId}`,
        action: log.action,
        targetType: log.targetType,
        targetId: '',
        targetName: '',
        description: log.batchDesc || log.batchId,
        operatorId: log.operatorId,
        operatorName: log.operatorName,
        operatorRole: log.operatorRole,
        createdAt: log.createdAt,
        batchId: log.batchId,
        batchDesc: log.batchDesc,
        batchCount: 0,
        children: [],
      }
      batchMap.set(log.batchId, parent)
      rows.push(parent)
    }

    parent.children!.push({ ...log })
    parent.batchCount = parent.children!.length
    // Use the latest timestamp from children
    if (log.createdAt > parent.createdAt) {
      parent.createdAt = log.createdAt
    }
  }

  return rows
})

// ==================== 方法 ====================

function getRoleTagType(role: string): string {
  switch (role) {
    case 'admin': return 'danger'
    case 'developer': return ''
    case 'guest': return 'info'
    default: return 'info'
  }
}

function formatDate(value: string): string {
  if (!value) return '-'
  try {
    const date = new Date(value)
    if (isNaN(date.getTime())) return value
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    const h = String(date.getHours()).padStart(2, '0')
    const min = String(date.getMinutes()).padStart(2, '0')
    const sec = String(date.getSeconds()).padStart(2, '0')
    return `${y}-${m}-${d} ${h}:${min}:${sec}`
  } catch {
    return value
  }
}

function getRowClassName({ row }: { row: OperationLogRow }): string {
  if (row.batchCount) return 'batch-parent-row'
  return ''
}

function buildQuery() {
  const query: Record<string, any> = {
    page: currentPage.value,
    pageSize: pageSize.value,
  }
  if (filterAction.value) query.action = filterAction.value
  if (filterTargetType.value) query.targetType = filterTargetType.value
  if (filterOperatorName.value) query.operatorName = filterOperatorName.value
  if (dateRange.value) {
    query.startTime = dateRange.value[0]
    query.endTime = dateRange.value[1]
  }
  return query
}

async function loadLogs(): Promise<void> {
  loading.value = true
  try {
    const result = await getOperationLogs(buildQuery())
    logList.value = result.items
    total.value = result.total
  } catch {
    ElMessage.error('加载操作日志失败')
  } finally {
    loading.value = false
  }
}

function handleSearch(): void {
  currentPage.value = 1
  loadLogs()
}

function handleReset(): void {
  dateRange.value = null
  filterAction.value = ''
  filterTargetType.value = ''
  filterOperatorName.value = ''
  currentPage.value = 1
  loadLogs()
}

function handleSizeChange(): void {
  currentPage.value = 1
  loadLogs()
}

function handlePageChange(): void {
  loadLogs()
}

async function handleExport(): Promise<void> {
  exportLoading.value = true
  try {
    await exportOperationLogs(buildQuery())
    ElMessage.success('导出成功')
  } catch {
    ElMessage.error('导出失败')
  } finally {
    exportLoading.value = false
  }
}

function handleDeleteConfirm(row: OperationLog): void {
  deleteTarget.value = row
  deleteDialogVisible.value = true
}

async function handleDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deleteOperationLog(deleteTarget.value.id)
    ElMessage.success('删除成功')
    deleteDialogVisible.value = false
    await loadLogs()
  } catch {
    // Error shown by interceptor
  }
}

// ==================== 生命周期 ====================

onMounted(() => {
  loadLogs()
})
</script>

<style scoped lang="scss">
.operation-log {
  height: 100%;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;

  h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    color: #303133;
  }
}

.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.batch-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: #409eff;

  .batch-icon {
    font-size: 16px;
  }

  .batch-count {
    margin-left: 4px;
  }
}

:deep(.batch-parent-row) {
  background-color: #f0f7ff !important;

  td:first-child {
    border-left: 3px solid #409eff;
  }
}

.main-branch {
  color: #909399;
  font-size: 12px;
}
</style>
