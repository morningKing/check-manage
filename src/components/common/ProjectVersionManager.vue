<template>
  <el-dialog
    v-model="visible"
    title="项目版本管理"
    width="85%"
    top="5vh"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <div class="project-version-manager">
      <el-card class="header-card" shadow="never">
        <template #header>
          <div class="card-header">
            <div class="header-left">
              <span class="title">当前分支</span>
              <el-tag v-if="currentBranch" :type="currentBranch.branchId === 'main' ? 'success' : 'warning'">
                {{ currentBranch.branchName }}
              </el-tag>
              <el-tag v-if="currentBranch?.branchId === 'main' && mainBranchLocked" type="danger" size="small">
                🔒 已锁定
              </el-tag>
              <el-button
                v-if="currentBranch && currentBranch.branchId !== 'main'"
                type="primary"
                size="small"
                @click="handleSwitchToMain"
              >
                切换回主分支
              </el-button>
              <el-button
                v-if="currentBranch?.branchId === 'main'"
                :type="mainBranchLocked ? 'info' : 'warning'"
                size="small"
                @click="handleMainBranchLock"
              >
                {{ mainBranchLocked ? '解锁主分支' : '锁定主分支' }}
              </el-button>
            </div>
            <div class="header-actions">
              <el-button type="primary" size="small" @click="showCreateDialog">
                <el-icon><Plus /></el-icon>
                创建版本
              </el-button>
              <el-button size="small" @click="refreshData">
                <el-icon><Refresh /></el-icon>
                刷新
              </el-button>
            </div>
          </div>
        </template>

        <el-table v-loading="loading" :data="versions" stripe style="width: 100%">
          <el-table-column prop="name" label="名称" min-width="150" />
          <el-table-column prop="versionType" label="类型" width="100">
            <template #default="{ row }">
              <el-tag :type="row.versionType === 'branch' ? 'warning' : ''" size="small">
                {{ row.versionType === 'branch' ? '分支' : '快照' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="120">
            <template #default="{ row }">
              <el-tag v-if="row.isLocked" type="danger" size="small" style="margin-right: 4px">
                🔒 已锁定
              </el-tag>
              <el-tag :type="getStatusTagType(row.status)" size="small">
                {{ getStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="recordsCount" label="记录数" width="100" />
          <el-table-column prop="createdBy" label="创建者" width="120" />
          <el-table-column prop="createdAt" label="创建时间" width="180">
            <template #default="{ row }">
              {{ formatDateTime(row.createdAt) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="280" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.versionType === 'branch' && row.status === 'active' && row.id !== currentBranch?.branchId"
                type="primary"
                size="small"
                link
                @click="handleSwitchBranch(row)"
              >
                切换
              </el-button>
              <el-button
                v-if="row.status === 'active' && row.versionType === 'branch'"
                type="success"
                size="small"
                link
                @click="showMergeDialog(row)"
              >
                合并
              </el-button>
              <el-button
                v-if="row.versionType === 'branch' && row.status === 'active'"
                :type="row.isLocked ? 'info' : 'warning'"
                size="small"
                link
                @click="handleLock(row)"
              >
                {{ row.isLocked ? '解锁' : '锁定' }}
              </el-button>
              <el-button size="small" link @click="showDiffDialog(row)">对比</el-button>
              <el-button size="small" link @click="showDetail(row)">详情</el-button>
              <el-button
                v-if="!row.isProtected"
                type="danger"
                size="small"
                link
                @click="handleDelete(row)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-if="total > pageSize"
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          style="margin-top: 16px; justify-content: flex-end"
        />
      </el-card>
    </div>

    <!-- 创建版本对话框 -->
    <el-dialog v-model="createDialogVisible" title="创建项目版本" width="500px" append-to-body>
      <el-form :model="createForm" label-width="100px">
        <el-form-item label="版本名称" required>
          <el-input v-model="createForm.name" placeholder="请输入版本名称" />
        </el-form-item>
        <el-form-item label="版本类型" required>
          <el-radio-group v-model="createForm.versionType">
            <el-radio value="snapshot">快照（只读）</el-radio>
            <el-radio value="branch">分支（可编辑）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="版本描述">
          <el-input v-model="createForm.description" type="textarea" :rows="3" placeholder="请输入版本描述" />
        </el-form-item>
        <el-form-item v-if="createForm.versionType === 'branch'" label="父版本">
          <el-select v-model="createForm.parentVersion" clearable placeholder="选择父版本（可选）">
            <el-option v-for="v in versions" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 版本详情对话框 -->
    <el-dialog v-model="detailDialogVisible" title="版本详情" width="600px" append-to-body>
      <el-descriptions v-if="detailData" :column="2" border>
        <el-descriptions-item label="版本名称">{{ detailData.name }}</el-descriptions-item>
        <el-descriptions-item label="版本类型">
          <el-tag :type="detailData.versionType === 'branch' ? 'warning' : ''" size="small">
            {{ detailData.versionType === 'branch' ? '分支' : '快照' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="getStatusTagType(detailData.status)" size="small">
            {{ getStatusLabel(detailData.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="记录数">{{ detailData.recordsCount }}</el-descriptions-item>
        <el-descriptions-item label="创建者">{{ detailData.createdBy }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatDateTime(detailData.createdAt) }}</el-descriptions-item>
        <el-descriptions-item label="描述" :span="2">{{ detailData.description || '无' }}</el-descriptions-item>
      </el-descriptions>

      <div v-if="detailData?.collections?.length" style="margin-top: 16px">
        <h4>包含的数据集</h4>
        <el-table :data="detailData.collections" stripe size="small">
          <el-table-column prop="pageName" label="页面名称" />
          <el-table-column prop="collection" label="Collection" />
        </el-table>
      </div>

      <template #footer>
        <el-button @click="detailDialogVisible = false">关闭</el-button>
        <el-button
          v-if="detailData && detailData.status === 'active'"
          type="primary"
          @click="handleRestore(detailData)"
        >
          从此版本恢复
        </el-button>
      </template>
    </el-dialog>

    <!-- 版本对比对话框 -->
    <el-dialog v-model="diffDialogVisible" title="版本对比" width="80%" top="5vh" append-to-body>
      <el-form :inline="true" style="margin-bottom: 16px">
        <el-form-item label="对比版本">
          <el-select v-model="diffTargetVersion" placeholder="选择对比版本" style="width: 200px">
            <el-option label="主分支 (main)" value="main" />
            <el-option label="当前分支" value="current" />
            <el-option v-for="v in versions" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="diffLoading" @click="loadDiff">对比</el-button>
        </el-form-item>
      </el-form>

      <div v-if="diffResult" class="diff-result">
        <el-alert type="info" :closable="false" style="margin-bottom: 16px">
          <template #title>
            共 {{ diffResult.totalAdded }} 条新增，{{ diffResult.totalRemoved }} 条删除，{{ diffResult.totalModified }} 条修改
          </template>
        </el-alert>

        <el-collapse v-model="diffCollapseActive">
          <el-collapse-item
            v-for="coll in diffResult.collections"
            :key="coll.collection"
            :name="coll.collection"
          >
            <template #title>
              <span style="font-weight: 600">{{ coll.pageName }} ({{ coll.collection }})</span>
              <span style="margin-left: 12px; color: #67c23a">+{{ coll.added.length }}</span>
              <span style="margin-left: 8px; color: #f56c6c">-{{ coll.removed.length }}</span>
              <span style="margin-left: 8px; color: #e6a23c">~{{ coll.modified.length }}</span>
            </template>

            <!-- 新增记录 -->
            <div v-if="coll.added.length" class="diff-section">
              <h5 style="color: #67c23a">新增记录 ({{ coll.added.length }})</h5>
              <el-table :data="coll.added" stripe size="small" max-height="200">
                <el-table-column prop="id" label="ID" width="150" />
                <el-table-column prop="name" label="名称" />
              </el-table>
            </div>

            <!-- 删除记录 -->
            <div v-if="coll.removed.length" class="diff-section">
              <h5 style="color: #f56c6c">删除记录 ({{ coll.removed.length }})</h5>
              <el-table :data="coll.removed" stripe size="small" max-height="200">
                <el-table-column prop="id" label="ID" width="150" />
                <el-table-column prop="name" label="名称" />
              </el-table>
            </div>

            <!-- 修改记录 -->
            <div v-if="coll.modified.length" class="diff-section">
              <h5 style="color: #e6a23c">修改记录 ({{ coll.modified.length }})</h5>
              <el-table :data="coll.modified" stripe size="small" max-height="300">
                <el-table-column prop="id" label="ID" width="150" />
                <el-table-column label="变更字段">
                  <template #default="{ row }">
                    <div v-for="field in row.fields" :key="field.fieldName" class="field-change">
                      <span class="field-name">{{ field.fieldName }}:</span>
                      <span class="old-value">{{ field.oldValue }}</span>
                      <span style="margin: 0 4px">→</span>
                      <span class="new-value">{{ field.newValue }}</span>
                    </div>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <div v-if="coll.unchangedCount" style="color: #909399; margin-top: 8px">
              未变更记录: {{ coll.unchangedCount }} 条
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-dialog>

    <!-- 版本合并对话框 -->
    <BeyondCompareMerge
      v-model="mergeDialogVisible"
      :project-menu-id="projectMenuId"
      :version-id="mergeTarget?.id || ''"
      :version-name="mergeTarget?.name || ''"
      :source-version="mergeTarget"
      @success="handleMergeSuccess"
    />
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import {
  listProjectVersions,
  createProjectVersion,
  switchProjectBranch,
  deleteProjectVersion,
  getProjectVersionDetail,
  getCurrentProjectBranch,
  diffProjectVersions,
  restoreProjectVersion,
  switchToMainProjectBranch,
  getProjectVersionDeleteImpact,
  lockProjectVersion,
  unlockProjectVersion,
  lockMainBranch,
  unlockMainBranch,
} from '@/api/projectVersion'
import type { ProjectVersion, ProjectVersionFormData } from '@/types/version'
import type { DiffResult } from '@/api/projectVersion'
import { useAuthStore } from '@/stores/auth'
import BeyondCompareMerge from './BeyondCompareMerge.vue'

const props = defineProps<{
  modelValue: boolean
  projectMenuId: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'refresh'): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

const authStore = useAuthStore()

const loading = ref(false)
const versions = ref<ProjectVersion[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const currentBranch = ref<{ branchId: string; branchName: string; mainLocked?: boolean; mainLockedBy?: string } | null>(null)
const mainBranchLocked = ref(false)

const createDialogVisible = ref(false)
const createForm = ref<ProjectVersionFormData>({
  projectMenuId: props.projectMenuId,
  name: '',
  description: '',
  versionType: 'snapshot',
  createdBy: authStore.user?.username || '',
  parentVersion: undefined,
})
const creating = ref(false)

const detailDialogVisible = ref(false)
const detailData = ref<ProjectVersion | null>(null)

const diffDialogVisible = ref(false)
const diffSourceVersion = ref<string>('')
const diffTargetVersion = ref<string>('main')
const diffResult = ref<DiffResult | null>(null)
const diffLoading = ref(false)
const diffCollapseActive = ref<string[]>([])

const mergeDialogVisible = ref(false)
const mergeTarget = ref<ProjectVersion | null>(null)

function getStatusTagType(status: string): string {
  const map: Record<string, string> = {
    active: 'success',
    merged: 'info',
    archived: 'warning',
  }
  return map[status] || ''
}

function getStatusLabel(status: string): string {
  const map: Record<string, string> = {
    active: '活跃',
    merged: '已合并',
    archived: '已归档',
  }
  return map[status] || status
}

function formatDateTime(dateStr?: string): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN')
}

async function refreshData() {
  loading.value = true
  try {
    const [versionResult, branchResult] = await Promise.all([
      listProjectVersions(props.projectMenuId, currentPage.value, pageSize.value),
      getCurrentProjectBranch(props.projectMenuId),
    ])
    versions.value = versionResult.items
    total.value = versionResult.total
    currentBranch.value = branchResult
    mainBranchLocked.value = branchResult.mainLocked || false
  } catch (err: any) {
    ElMessage.error(err.message || '获取数据失败')
  } finally {
    loading.value = false
  }
}

function showCreateDialog() {
  createForm.value = {
    projectMenuId: props.projectMenuId,
    name: '',
    description: '',
    versionType: 'snapshot',
    createdBy: authStore.user?.username || '',
    parentVersion: undefined,
  }
  createDialogVisible.value = true
}

async function handleCreate() {
  if (!createForm.value.name) {
    ElMessage.warning('请输入版本名称')
    return
  }

  creating.value = true
  try {
    await createProjectVersion(createForm.value)
    ElMessage.success('版本创建成功')
    createDialogVisible.value = false
    refreshData()
  } catch (err: any) {
    ElMessage.error(err.message || '创建失败')
  } finally {
    creating.value = false
  }
}

async function handleSwitchBranch(version: ProjectVersion) {
  try {
    await ElMessageBox.confirm(
      `确定切换到分支「${version.name}」？这将同步切换项目下所有数据集的分支状态。`,
      '切换分支',
      { type: 'warning' }
    )

    await switchProjectBranch(version.id, props.projectMenuId)
    ElMessage.success('分支切换成功')
    refreshData()
    emit('refresh')
  } catch (err: any) {
    if (err !== 'cancel') {
      ElMessage.error(err.message || '切换失败')
    }
  }
}

async function handleSwitchToMain() {
  try {
    await ElMessageBox.confirm(
      '确定切换回主分支？这将同步切换项目下所有数据集的分支状态。',
      '切换主分支',
      { type: 'warning' }
    )

    await switchToMainProjectBranch(props.projectMenuId)
    ElMessage.success('已切换回主分支')
    refreshData()
    emit('refresh')
  } catch (err: any) {
    if (err !== 'cancel') {
      ElMessage.error(err.message || '切换失败')
    }
  }
}

async function showDetail(version: ProjectVersion) {
  try {
    const detail = await getProjectVersionDetail(version.id)
    detailData.value = detail
    detailDialogVisible.value = true
  } catch (err: any) {
    ElMessage.error(err.message || '获取详情失败')
  }
}

function showDiffDialog(version: ProjectVersion) {
  diffSourceVersion.value = version.id
  diffTargetVersion.value = 'main'
  diffResult.value = null
  diffDialogVisible.value = true
}

async function loadDiff() {
  diffLoading.value = true
  try {
    const result = await diffProjectVersions(
      props.projectMenuId,
      'main',
      diffTargetVersion.value
    )
    diffResult.value = result
    // 默认展开所有collection
    diffCollapseActive.value = result.collections.map(c => c.collection)
  } catch (err: any) {
    ElMessage.error(err.message || '对比失败')
  } finally {
    diffLoading.value = false
  }
}

function showMergeDialog(version: ProjectVersion) {
  mergeTarget.value = version
  mergeDialogVisible.value = true
}

function handleMergeSuccess() {
  mergeDialogVisible.value = false
  refreshData()
  emit('refresh')
}

async function handleRestore(version: ProjectVersion) {
  try {
    await ElMessageBox.confirm(
      `确定从版本「${version.name}」恢复数据？这将覆盖当前分支的所有数据。`,
      '恢复版本',
      { type: 'warning' }
    )

    await restoreProjectVersion(version.id, props.projectMenuId)
    ElMessage.success('数据已恢复')
    detailDialogVisible.value = false
    emit('refresh')
  } catch (err: any) {
    if (err !== 'cancel') {
      ElMessage.error(err.message || '恢复失败')
    }
  }
}

async function handleDelete(version: ProjectVersion) {
  try {
    // 获取删除影响报告
    const impact = await getProjectVersionDeleteImpact(version.id)

    if (!impact.canDelete) {
      ElMessage.warning(impact.warningMessage)
      return
    }

    await ElMessageBox.confirm(
      impact.warningMessage + '\n\n确定删除？此操作不可恢复。',
      '删除版本',
      { type: 'warning' }
    )

    await deleteProjectVersion(version.id)
    ElMessage.success('版本已删除')
    refreshData()
  } catch (err: any) {
    if (err !== 'cancel') {
      ElMessage.error(err.message || '删除失败')
    }
  }
}

async function handleLock(version: ProjectVersion) {
  try {
    if (version.isLocked) {
      await ElMessageBox.confirm(
        `确定解锁分支「${version.name}」？解锁后该分支可正常编辑。`,
        '解锁分支',
        { type: 'info' }
      )
      await unlockProjectVersion(version.id)
      ElMessage.success('分支已解锁')
    } else {
      const result = await ElMessageBox.prompt(
        '请输入锁定原因（可选）',
        '锁定分支',
        {
          confirmButtonText: '锁定',
          cancelButtonText: '取消',
          inputPlaceholder: '例如：发布前冻结',
          type: 'warning',
        }
      ).catch(() => ({ value: null }))

      if (result.value === null) return // 用户取消

      await lockProjectVersion(version.id, result.value)
      ElMessage.success('分支已锁定')
    }
    refreshData()
  } catch (err: any) {
    if (err !== 'cancel') {
      ElMessage.error(err.message || '操作失败')
    }
  }
}

async function handleMainBranchLock() {
  try {
    if (mainBranchLocked.value) {
      await ElMessageBox.confirm(
        '确定解锁主分支？解锁后主分支可正常编辑。',
        '解锁主分支',
        { type: 'info' }
      )
      await unlockMainBranch(props.projectMenuId)
      ElMessage.success('主分支已解锁')
    } else {
      const result = await ElMessageBox.prompt(
        '请输入锁定原因（可选）',
        '锁定主分支',
        {
          confirmButtonText: '锁定',
          cancelButtonText: '取消',
          inputPlaceholder: '例如：发布前冻结',
          type: 'warning',
        }
      ).catch(() => ({ value: null }))

      if (result.value === null) return // 用户取消

      await lockMainBranch(props.projectMenuId, result.value)
      ElMessage.success('主分支已锁定')
    }
    refreshData()
  } catch (err: any) {
    if (err !== 'cancel') {
      ElMessage.error(err.message || '操作失败')
    }
  }
}

onMounted(() => {
  refreshData()
})

// 当对话框打开时刷新数据
watch(visible, (newVal) => {
  if (newVal) {
    refreshData()
  }
})
</script>

<style scoped lang="scss">
.project-version-manager {
  .header-card {
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;

      .header-left {
        display: flex;
        align-items: center;
        gap: 12px;

        .title {
          font-size: 16px;
          font-weight: 600;
        }
      }
    }
  }
}

.diff-result {
  .diff-section {
    margin-top: 12px;

    h5 {
      margin-bottom: 8px;
    }
  }

  .field-change {
    margin: 4px 0;

    .field-name {
      font-weight: 500;
      margin-right: 4px;
    }

    .old-value {
      color: #f56c6c;
    }

    .new-value {
      color: #67c23a;
    }
  }
}
</style>