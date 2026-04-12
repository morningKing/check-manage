/**
 * 版本管理组件
 *
 * 职责：
 * - 显示当前集合的版本列表
 * - 创建版本快照/分支
 * - 切换分支（并行开发，带锁定机制）
 * - 对比版本差异
 * - 合并版本到当前数据
 * - 从版本恢复数据
 * - 删除版本
 */
<template>
  <el-drawer
    v-model="visible"
    title="版本管理"
    direction="rtl"
    size="700px"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <!-- 当前分支状态 -->
    <div v-if="currentBranch?.branchId" class="current-branch-bar">
      <el-tag type="success" effect="dark">
        <el-icon><FolderOpened /></el-icon>
        当前分支：{{ currentBranch.branchName }}
      </el-tag>
      <el-button type="primary" size="small" @click="handleSwitchToMain" :loading="switchingToMain">
        切换回主分支
      </el-button>
    </div>
    <div v-else class="current-branch-bar">
      <el-tag type="info">
        <el-icon><Document /></el-icon>
        主分支
      </el-tag>
      <span class="branch-hint">创建分支后可切换进行并行开发</span>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <el-button type="primary" @click="showCreateDialog = true">
        <el-icon><Plus /></el-icon>
        创建版本
      </el-button>
      <el-button @click="loadVersions" :loading="loading">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>

    <!-- 说明 -->
    <el-alert
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    >
      <template #title>
        <span>切换分支后，您将看到该分支的数据。不同用户可以同时在不同的分支上工作，互不影响。</span>
      </template>
    </el-alert>

    <!-- 版本列表 -->
    <el-table
      :data="versions"
      v-loading="loading"
      stripe
      size="small"
      class="version-table"
    >
      <el-table-column prop="name" label="版本名称" min-width="180">
        <template #default="{ row }">
          <div class="version-name">
            <span>{{ row.name }}</span>
            <el-tag v-if="row.versionType === 'branch'" type="warning" size="small">
              分支
            </el-tag>
            <el-tag v-if="isCurrentBranch(row)" type="success" size="small">
              当前
            </el-tag>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="70">
        <template #default="{ row }">
          <el-tag :type="getStatusTagType(row.status)" size="small">
            {{ STATUS_LABELS[row.status] || row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="数据" width="70">
        <template #default="{ row }">
          <span class="data-count">{{ row.recordsCount }} 条</span>
        </template>
      </el-table-column>
      <el-table-column prop="createdBy" label="创建者" width="80" />
      <el-table-column prop="createdAt" label="创建时间" width="110">
        <template #default="{ row }">
          {{ formatTime(row.createdAt) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button-group>
            <!-- 切换按钮：仅分支类型显示 -->
            <el-tooltip v-if="row.versionType === 'branch'" :content="getSwitchTooltip(row)">
              <el-button
                size="small"
                type="success"
                @click="handleSwitch(row)"
                :disabled="!canSwitch(row)"
              >
                <el-icon><Switch /></el-icon>
              </el-button>
            </el-tooltip>
            <el-tooltip content="对比">
              <el-button size="small" @click="handleDiff(row)" :disabled="row.status === 'merged'">
                <el-icon><Sort /></el-icon>
              </el-button>
            </el-tooltip>
            <el-tooltip content="合并到当前">
              <el-button
                size="small"
                @click="handleMerge(row)"
                :disabled="row.status === 'merged'"
              >
                <el-icon><Connection /></el-icon>
              </el-button>
            </el-tooltip>
            <el-tooltip content="恢复">
              <el-button
                size="small"
                type="warning"
                @click="handleRestore(row)"
                :disabled="row.status === 'merged'"
              >
                <el-icon><RefreshRight /></el-icon>
              </el-button>
            </el-tooltip>
            <el-tooltip content="删除">
              <el-button
                size="small"
                type="danger"
                @click="handleDelete(row)"
                :disabled="row.isProtected || row.status === 'merged'"
                :loading="deletingVersionId === row.id"
              >
                <el-icon><Delete /></el-icon>
              </el-button>
            </el-tooltip>
          </el-button-group>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空状态 -->
    <el-empty v-if="!loading && versions.length === 0" description="暂无版本快照" />

    <!-- 创建版本对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      title="创建版本"
      width="450px"
      :close-on-click-modal="false"
    >
      <el-form :model="createForm" label-width="80px">
        <el-form-item label="版本名称" required>
          <el-input v-model="createForm.name" placeholder="如：v1.0-release 或 feature-xxx" />
        </el-form-item>
        <el-form-item label="版本类型">
          <el-radio-group v-model="createForm.versionType">
            <el-radio value="snapshot">
              <el-tooltip content="只读快照，用于保存历史状态" placement="top">
                <span>快照</span>
              </el-tooltip>
            </el-radio>
            <el-radio value="branch">
              <el-tooltip content="可切换的分支，用于并行开发" placement="top">
                <span>分支</span>
              </el-tooltip>
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="createForm.description"
            type="textarea"
            :rows="3"
            placeholder="版本描述（可选）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="handleCreate" :loading="creating">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 对比对话框 -->
    <BackupDiffDialog
      v-model="showDiffDialog"
      :collection="collection"
      :page-name="pageName"
      :base-source="diffBaseSource"
      :target-source="diffTargetSource"
      @merge="handleMergeFromDiff"
    />

    <!-- 合并对话框 (Beyond Compare 风格) -->
    <BeyondCompareMerge
      v-model="showMergeConflictDialog"
      :collection="collection"
      :source-version="mergeConflictTarget"
      @success="handleMergeSuccess"
    />

    <!-- 切换分支确认对话框 -->
    <el-dialog
      v-model="showSwitchDialog"
      title="切换分支"
      width="550px"
      :close-on-click-modal="false"
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      >
        <template #default>
          <p><b>切换说明：</b></p>
          <ol style="margin: 8px 0; padding-left: 20px;">
            <li>切换后您将看到该分支的数据</li>
            <li>在分支上的编辑不会影响主分支或其他分支</li>
            <li>完成编辑后可以合并回主分支</li>
          </ol>
          <p>目标分支：<b>{{ switchTarget?.name }}</b></p>
        </template>
      </el-alert>
      <div v-if="switchResult?.affectedCollections?.length > 1" style="margin-top: 12px">
        <el-divider />
        <p><b>影响的集合：</b></p>
        <el-tag
          v-for="coll in switchResult.affectedCollections"
          :key="coll"
          style="margin: 4px"
          type="warning"
        >
          {{ getCollectionDisplayName(coll) }}
        </el-tag>
        <p style="color: #909399; font-size: 13px; margin-top: 8px">
          以上集合将一起切换到分支 <b>{{ switchTarget?.name }}</b>
        </p>
      </div>
      <template #footer>
        <el-button @click="showSwitchDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmSwitch" :loading="switching">
          确认切换
        </el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Sort, RefreshRight, Delete, Connection, Switch, FolderOpened, Document } from '@element-plus/icons-vue'
import { del } from '@/utils/request'
import BackupDiffDialog from './BackupDiffDialog.vue'
import BeyondCompareMerge from './BeyondCompareMerge.vue'
import {
  getVersions,
  createVersion,
  restoreVersion,
  switchToVersion,
  switchToMainBranch,
  getCurrentBranch,
  type UserBranch,
} from '@/api/version'
import type { CollectionVersion, CreateVersionRequest } from '@/types'

// ==================== Props & Emits ====================

interface Props {
  modelValue: boolean
  collection: string
  pageName: string
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'refresh', affectedCollections?: string[]): void
}>()

// ==================== Constants ====================

const STATUS_LABELS: Record<string, string> = {
  active: '活跃',
  merged: '已合并',
  archived: '已归档',
}

// ==================== State ====================

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const versions = ref<CollectionVersion[]>([])
const currentBranch = ref<UserBranch | null>(null)
const loading = ref(false)
const deletingVersionId = ref<string | null>(null)

// 创建版本
const showCreateDialog = ref(false)
const createForm = ref<CreateVersionRequest>({
  collection: '',
  name: '',
  description: '',
  versionType: 'snapshot',
})
const creating = ref(false)

// 对比
const showDiffDialog = ref(false)
const diffBaseSource = ref('current')
const diffTargetSource = ref('')

// 合并
const showMergeConflictDialog = ref(false)
const mergeConflictTarget = ref<CollectionVersion | null>(null)

// 切换
const showSwitchDialog = ref(false)
const switchTarget = ref<CollectionVersion | null>(null)
const switching = ref(false)
const switchingToMain = ref(false)
const switchResult = ref<any>(null)

// ==================== Methods ====================

function getCollectionDisplayName(collection: string): string {
  const collectionNames: Record<string, string> = {
    'inspection-case': '巡检用例',
    'inspection-plan': '巡检计划',
    // Add more mappings as needed
  }
  return collectionNames[collection] || collection
}

function getStatusTagType(status: string): string {
  const map: Record<string, string> = {
    active: 'success',
    merged: 'info',
    archived: 'warning',
  }
  return map[status] || ''
}

function formatTime(iso: string): string {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function isCurrentBranch(row: CollectionVersion): boolean {
  return currentBranch.value?.branchId === row.id
}

function canSwitch(row: CollectionVersion): boolean {
  if (row.status !== 'active') return false
  if (isCurrentBranch(row)) return false
  return true
}

function getSwitchTooltip(row: CollectionVersion): string {
  if (row.status !== 'active') return '版本状态非活跃'
  if (isCurrentBranch(row)) return '当前已在此分支'
  return '切换到此分支'
}

async function loadVersions() {
  loading.value = true
  try {
    versions.value = await getVersions(props.collection)
    currentBranch.value = await getCurrentBranch(props.collection)
  } catch {
    versions.value = []
    currentBranch.value = null
  } finally {
    loading.value = false
  }
}

async function handleCreate() {
  if (!createForm.value.name) {
    ElMessage.warning('请输入版本名称')
    return
  }

  creating.value = true
  try {
    await createVersion({
      ...createForm.value,
      collection: props.collection,
    })
    ElMessage.success('版本创建成功')
    showCreateDialog.value = false
    createForm.value = {
      collection: '',
      name: '',
      description: '',
      versionType: 'snapshot',
    }
    loadVersions()
  } catch (e: any) {
    const msg = e?.response?.data?.error || '创建失败'
    ElMessage.error(msg)
  } finally {
    creating.value = false
  }
}

function handleDiff(row: CollectionVersion) {
  diffBaseSource.value = 'current'
  diffTargetSource.value = row.id
  showDiffDialog.value = true
}

function handleMergeFromDiff(versionId: string) {
  showDiffDialog.value = false
  const version = versions.value.find(v => v.id === versionId)
  if (version) {
    mergeConflictTarget.value = version
    showMergeConflictDialog.value = true
  }
}

function handleMerge(row: CollectionVersion) {
  mergeConflictTarget.value = row
  showMergeConflictDialog.value = true
}

function handleMergeSuccess() {
  showMergeConflictDialog.value = false
  loadVersions()
  emit('refresh')
  ElMessage.success('合并成功')
}

async function handleRestore(row: CollectionVersion) {
  try {
    await ElMessageBox.confirm(
      `确定要从版本「${row.name}」恢复数据吗？当前数据将被完全覆盖。`,
      '恢复确认',
      { type: 'warning', confirmButtonText: '确定恢复', cancelButtonText: '取消' }
    )

    const result = await restoreVersion(row.id)
    ElMessage.success(`恢复成功：${result.recordsCount} 条记录`)
    emit('refresh')
  } catch (e: any) {
    if (e !== 'cancel') {
      const msg = e?.response?.data?.error || '恢复失败'
      ElMessage.error(msg)
    }
  }
}

function handleSwitch(row: CollectionVersion) {
  switchTarget.value = row
  showSwitchDialog.value = true
}

async function confirmSwitch() {
  if (!switchTarget.value) return

  switching.value = true
  try {
    const result = await switchToVersion(switchTarget.value.id)
    switchResult.value = result

    // Enhanced success message
    let msg = `已切换到分支「${result.branchName}」，加载 ${result.recordsInBranch} 条记录`
    if (result.initialized) {
      msg += '（分支数据已从快照初始化）'
    }
    if (result.affectedCollections && result.affectedCollections.length > 1) {
      msg += `\n同时切换了 ${result.affectedCollections.length - 1} 个关联集合`
    }

    ElMessage.success(msg)
    showSwitchDialog.value = false
    loadVersions()

    // Emit refresh with affected collections list
    emit('refresh', result.affectedCollections)
  } catch (e: any) {
    const msg = e?.response?.data?.error || '切换失败'
    ElMessage.error(msg)
  } finally {
    switching.value = false
  }
}

async function handleSwitchToMain() {
  try {
    await ElMessageBox.confirm(
      '确定要切换回主分支吗？',
      '切换确认',
      { type: 'info', confirmButtonText: '确定', cancelButtonText: '取消' }
    )

    switchingToMain.value = true
    try {
      const result = await switchToMainBranch(props.collection)

      // Enhanced success message with affected collections
      let msg = `已切换回主分支，加载 ${result.recordsInBranch} 条记录`
      if (result.affectedCollections && result.affectedCollections.length > 1) {
        msg += `\n同时切换了 ${result.affectedCollections.length - 1} 个关联集合`
      }

      ElMessage.success(msg)
      loadVersions()

      // Emit refresh with affected collections list
      emit('refresh', result.affectedCollections)
    } catch (e: any) {
      const msg = e?.response?.data?.error || '切换失败'
      ElMessage.error(msg)
    } finally {
      switchingToMain.value = false
    }
  } catch {
    // 用户取消
  }
}

async function handleDelete(row: CollectionVersion) {
  try {
    deletingVersionId.value = row.id

    // Phase 1: Get impact report with confirmed=false
    const response = await del<{ requiresConfirmation: boolean; data: any }>(`/versions/${row.id}?confirmed=false`)

    if (response.requiresConfirmation) {
      const impact = response.data

      // Phase 2: Show confirmation dialog with impact details
      await ElMessageBox.confirm(
        generateDeleteWarningMessage(impact),
        '删除版本确认',
        {
          type: 'warning',
          confirmButtonText: '确认删除',
          cancelButtonText: '取消',
          customClass: 'delete-version-dialog'
        }
      )

      // Phase 3: User confirmed, execute deletion with confirmed=true
      await del(`/versions/${row.id}?confirmed=true`)
      ElMessage.success('版本已删除')
      loadVersions()
    } else {
      // No confirmation required, delete directly
      await del(`/versions/${row.id}?confirmed=true`)
      ElMessage.success('版本已删除')
      loadVersions()
    }
  } catch (e: any) {
    if (e !== 'cancel') {
      const msg = e?.response?.data?.error || '删除失败'
      ElMessage.error(msg)
    }
  } finally {
    deletingVersionId.value = null
  }
}

function generateDeleteWarningMessage(impact: any): string {
  let message = impact.warningMessage + '\n\n'

  if (impact.hasCrossCollectionData) {
    message += '将要删除的数据详情：\n'
    impact.affectedCollections.forEach((coll: any) => {
      message += `\n${coll.collection} (${coll.recordCount}条记录):\n`
      coll.records.slice(0, 5).forEach((rec: any) => {
        message += `  • ${rec.displayName}\n`
      })
      if (coll.recordCount > 5) {
        message += `  ...还有 ${coll.recordCount - 5} 条\n`
      }
    })
  }

  return message
}

// ==================== Watch ====================

watch(visible, (v) => {
  if (v) {
    loadVersions()
    createForm.value.collection = props.collection
  }
})
</script>

<style scoped lang="scss">
.current-branch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: #f5f7fa;
  border-radius: 4px;
  margin-bottom: 16px;

  .branch-hint {
    color: #909399;
    font-size: 13px;
  }
}

.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.version-table {
  width: 100%;
}

.version-name {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.data-count {
  color: #606266;
  font-size: 12px;
}
</style>