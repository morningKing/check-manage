/**
 * 版本合并对话框
 *
 * 单页统一视图设计：
 * - 无向导步骤，所有变更一页展示
 * - 新增/删除记录：复选框选择
 * - 修改记录：记录级选择 + 可展开的字段级选择
 */
<template>
  <el-dialog
    v-model="visible"
    :title="dialogTitle"
    width="1000px"
    top="5vh"
    :close-on-click-modal="false"
    destroy-on-close
    @close="handleClose"
  >
    <!-- 顶部批量操作按钮 -->
    <div v-if="!loading && !error && hasChanges" class="batch-actions">
      <el-button type="success" plain @click="handleAcceptAllSource">
        全部接受源版本
      </el-button>
      <el-button type="warning" plain @click="handleAcceptAllTarget">
        全部接受目标版本
      </el-button>
    </div>

    <!-- 统计条 -->
    <div v-if="!loading && !error && diffResult" class="statistics-bar">
      <span class="stat-item">
        <span class="stat-label">共</span>
        <span class="stat-value">{{ totalChanges }}</span>
        <span class="stat-label">处变更</span>
      </span>
      <span class="stat-divider">|</span>
      <span class="stat-item stat-added">
        新增 {{ diffResult.added.length }}
      </span>
      <span class="stat-item stat-removed">
        删除 {{ diffResult.removed.length }}
      </span>
      <span class="stat-item stat-modified">
        修改 {{ diffResult.modified.length }}
      </span>
      <span class="stat-divider">|</span>
      <span class="stat-item stat-selected">
        已选 {{ selectedCount }}
      </span>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <span>正在加载变更信息...</span>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-container">
      <el-icon :size="48" color="#f56c6c"><CircleCloseFilled /></el-icon>
      <div class="error-title">加载变更信息失败</div>
      <div class="error-message">{{ error }}</div>
      <el-button type="primary" @click="loadDiff">重试</el-button>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!hasChanges" class="empty-container">
      <el-empty description="没有需要处理的变更" />
    </div>

    <!-- 主内容区域 -->
    <div v-else class="main-content">
      <el-collapse v-model="activePanels">
        <!-- 新增记录 -->
        <MergeRecordSection
          v-if="diffResult"
          type="added"
          title="新增记录"
          name="added"
          :records="diffResult.added"
          :selected-ids="state.decisions.addedRecords"
          :fields="fields"
          @toggle="handleToggleAdded"
          @select-all="handleSelectAllAdded"
        />

        <!-- 删除记录 -->
        <MergeRecordSection
          v-if="diffResult"
          type="removed"
          title="删除记录"
          name="removed"
          :records="diffResult.removed"
          :selected-ids="state.decisions.removedRecords"
          :fields="fields"
          @toggle="handleToggleRemoved"
          @select-all="handleSelectAllRemoved"
        />

        <!-- 修改记录 -->
        <MergeModifiedSection
          v-if="diffResult"
          :records="diffResult.modified"
          :selected-records="state.decisions.modifiedRecords"
          :expanded-records="state.expandedRecords"
          :fields="fields"
          @toggle-record="handleToggleModified"
          @toggle-expand="handleToggleExpand"
          @field-select="handleFieldSelect"
          @set-all-fields="handleSetAllFields"
          @select-all="handleSelectAllModified"
        />
      </el-collapse>
    </div>

    <!-- 底部确认区 -->
    <template #footer>
      <div class="dialog-footer">
        <div class="footer-status">
          <span v-if="!hasSelection" class="status-warning">
            ⚠️ 请至少选择一项变更
          </span>
          <span v-else class="status-info">
            💡 已选择 {{ selectedCount }} 项变更：新增 {{ state.decisions.addedRecords.size }} / 删除 {{ state.decisions.removedRecords.size }} / 修改 {{ state.decisions.modifiedRecords.size }}
          </span>
        </div>
        <div class="footer-actions">
          <el-button @click="handleCancel">取消</el-button>
          <el-button
            type="primary"
            :loading="submitting"
            :disabled="!hasSelection"
            @click="handleSubmit"
          >
            确认合并
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>

  <!-- 关闭确认对话框 -->
  <el-dialog
    v-model="showCloseConfirm"
    title="提示"
    width="400px"
    append-to-body
  >
    <p>您已选择了部分变更，关闭后将丢失这些选择。是否确认关闭？</p>
    <template #footer>
      <el-button @click="showCloseConfirm = false">取消</el-button>
      <el-button type="primary" @click="confirmClose">确认关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading, CircleCloseFilled } from '@element-plus/icons-vue'
import { diffVersions } from '@/api/version'
import { useMergeState } from './composables/useMergeState'
import MergeRecordSection from './MergeRecordSection.vue'
import MergeModifiedSection from './MergeModifiedSection.vue'
import type { CollectionVersion } from '@/types/version'
import type { FieldConfig } from '@/types'

interface Props {
  modelValue: boolean
  collection: string
  sourceVersion: CollectionVersion | null
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'success'): void
}>()

const {
  state,
  hasChanges,
  selectedCount,
  setSourceVersion,
  setDiffResult,
  toggleAddedRecord,
  toggleRemovedRecord,
  toggleModifiedRecord,
  setFieldDecision,
  toggleRecordExpanded,
  selectAllAdded,
  selectAllRemoved,
  selectAllModified,
  acceptAllSource,
  acceptAllTarget,
  setAllFieldsForRecord,
  submitMerge,
  reset,
} = useMergeState()

const loading = ref(false)
const error = ref('')
const submitting = ref(false)
const fields = ref<FieldConfig[]>([])
const loadAborted = ref(false)
const showCloseConfirm = ref(false)
const activePanels = ref<string[]>(['added', 'removed', 'modified'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const dialogTitle = computed(() => {
  if (props.sourceVersion) {
    return `版本合并 - 将「${props.sourceVersion.name}」合并到「当前工作区」`
  }
  return '版本合并'
})

const diffResult = computed(() => state.diffResult)

const totalChanges = computed(() => {
  if (!state.diffResult) return 0
  return state.diffResult.added.length + state.diffResult.removed.length + state.diffResult.modified.length
})

const hasSelection = computed(() => selectedCount.value > 0)

async function loadDiff(): Promise<void> {
  if (!props.sourceVersion) {
    error.value = '未指定源版本'
    return
  }

  loading.value = true
  error.value = ''
  loadAborted.value = false

  try {
    const result = await diffVersions({
      collection: props.collection,
      baseVersion: 'current',
      targetVersion: props.sourceVersion.id,
    })

    if (loadAborted.value) return

    fields.value = result.fields || []
    setDiffResult(result)
    setSourceVersion(props.sourceVersion)
  } catch (e: any) {
    if (loadAborted.value) return
    const msg = e?.response?.data?.error || e?.message || '加载差异信息失败'
    error.value = msg
    ElMessage.error(msg)
  } finally {
    if (!loadAborted.value) {
      loading.value = false
    }
  }
}

function handleToggleAdded(recordId: string, _selected: boolean): void {
  toggleAddedRecord(recordId)
}

function handleToggleRemoved(recordId: string, _selected: boolean): void {
  toggleRemovedRecord(recordId)
}

function handleToggleModified(recordId: string, _selected: boolean): void {
  toggleModifiedRecord(recordId)
}

function handleToggleExpand(recordId: string): void {
  toggleRecordExpanded(recordId)
}

function handleFieldSelect(recordId: string, fieldName: string, choice: 'source' | 'target'): void {
  setFieldDecision(recordId, fieldName, choice)
}

function handleSelectAllAdded(selected: boolean): void {
  selectAllAdded(selected)
}

function handleSelectAllRemoved(selected: boolean): void {
  selectAllRemoved(selected)
}

function handleSelectAllModified(selected: boolean): void {
  selectAllModified(selected)
}

function handleSetAllFields(recordId: string, choice: 'source' | 'target'): void {
  setAllFieldsForRecord(recordId, choice)
}

function handleAcceptAllSource(): void {
  acceptAllSource()
}

function handleAcceptAllTarget(): void {
  acceptAllTarget()
}

function handleCancel(): void {
  if (hasSelection.value) {
    showCloseConfirm.value = true
  } else {
    visible.value = false
  }
}

function confirmClose(): void {
  showCloseConfirm.value = false
  visible.value = false
}

function handleClose(): void {
  reset()
  fields.value = []
  error.value = ''
  loadAborted.value = true
}

async function handleSubmit(): Promise<void> {
  if (!hasSelection.value) {
    ElMessage.warning('请至少选择一项变更')
    return
  }

  submitting.value = true

  try {
    const result = await submitMerge()
    if (result.success) {
      ElMessage.success(`合并成功，共处理 ${result.merged_count} 项变更`)
      emit('success')
      visible.value = false
    }
  } catch (e: any) {
    const msg = e?.response?.data?.message || e?.message || '合并失败'
    ElMessage.error(msg)
  } finally {
    submitting.value = false
  }
}

watch(visible, (v) => {
  if (v && props.sourceVersion) {
    reset()
    loadDiff()
  } else if (!v) {
    loadAborted.value = true
  }
})
</script>

<style scoped lang="scss">
.batch-actions {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid #ebeef5;
}

.statistics-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background-color: #f5f7fa;
  border-radius: 4px;
  margin-bottom: 16px;
  font-size: 14px;
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.stat-value {
  font-weight: 600;
  color: #303133;
}

.stat-label {
  color: #606266;
}

.stat-divider {
  color: #dcdfe6;
}

.stat-added {
  color: #67c23a;
}

.stat-removed {
  color: #f56c6c;
}

.stat-modified {
  color: #e6a23c;
}

.stat-selected {
  color: #409eff;
  font-weight: 500;
}

.loading-container,
.error-container,
.empty-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  gap: 12px;
}

.error-title {
  font-size: 16px;
  font-weight: 500;
  color: #303133;
}

.error-message {
  font-size: 14px;
  color: #909399;
}

.main-content {
  max-height: 60vh;
  overflow-y: auto;
}

.dialog-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.footer-status {
  font-size: 14px;
}

.status-warning {
  color: #f56c6c;
}

.status-info {
  color: #606266;
}

.footer-actions {
  display: flex;
  gap: 12px;
}

.is-loading {
  animation: rotating 2s linear infinite;
}

@keyframes rotating {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>