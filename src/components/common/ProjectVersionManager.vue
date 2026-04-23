<template>
  <el-dialog
    v-model="visible"
    title="项目版本管理"
    width="85%"
    top="5vh"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <!-- 标签页切换 -->
    <el-tabs v-model="activeTab" class="manager-tabs">
      <el-tab-pane label="版本管理" name="versions">
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
      </el-tab-pane>

      <!-- 依赖管理标签页 -->
      <el-tab-pane label="项目依赖" name="dependencies">
        <ProjectDependencyManager
          :project-menu-id="projectMenuId"
          :current-branch="currentBranch"
        />
      </el-tab-pane>
    </el-tabs>

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

    <!-- 版本对比对话框 - Beyond Compare 风格 -->
    <el-dialog
      v-model="diffDialogVisible"
      title="版本对比"
      fullscreen
      append-to-body
      class="diff-dialog-fullscreen"
    >
      <!-- 顶部工具栏 -->
      <div class="diff-toolbar">
        <div class="toolbar-left">
          <span class="version-label">源版本：</span>
          <el-tag type="primary">{{ getVersionName(diffSourceVersion) }}</el-tag>
          <span class="version-label" style="margin-left: 24px">目标版本：</span>
          <el-select v-model="diffTargetVersion" placeholder="选择对比目标版本" style="width: 200px">
            <el-option label="主分支 (main)" value="main" />
            <el-option label="当前分支" value="current" />
            <el-option v-for="v in versions" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
          <el-button type="primary" :loading="diffLoading" style="margin-left: 12px" @click="loadDiff">
            对比
          </el-button>
        </div>
        <div class="toolbar-right">
          <el-button v-if="diffResult" type="success" @click="handleMergeFromDiff">
            合并到目标版本
          </el-button>
        </div>
      </div>

      <!-- Collection 选择器 -->
      <div v-if="diffResult && diffResult.collections && diffResult.collections.length > 1" class="diff-collection-tabs">
        <el-radio-group v-model="selectedDiffCollection" size="small">
          <el-radio-button v-for="coll in diffResult.collections" :key="coll.collection" :value="coll.collection">
            {{ coll.pageName }}
            <span class="diff-count added">+{{ coll.added.length }}</span>
            <span class="diff-count removed">-{{ coll.removed.length }}</span>
            <span class="diff-count modified">~{{ coll.modified.length }}</span>
          </el-radio-button>
        </el-radio-group>
      </div>

      <!-- 统计条 -->
      <div v-if="diffResult" class="diff-stats-bar">
        <span class="stat-item">
          共
          <span class="stat-value">{{ diffResult.totalAdded + diffResult.totalRemoved + diffResult.totalModified }}</span>
          处变更
        </span>
        <span class="stat-divider">|</span>
        <span class="stat-item stat-added">新增 {{ diffResult.totalAdded }}</span>
        <span class="stat-item stat-removed">删除 {{ diffResult.totalRemoved }}</span>
        <span class="stat-item stat-modified">修改 {{ diffResult.totalModified }}</span>
      </div>

      <!-- 加载状态 -->
      <div v-if="diffLoading" class="diff-loading">
        <el-icon class="is-loading" :size="32"><Loading /></el-icon>
        <span>正在对比版本...</span>
      </div>

      <!-- 左右对比视图 -->
      <div v-else-if="currentDiffCollection" class="diff-compare-container">
        <!-- 左侧：源版本 -->
        <div class="diff-panel diff-panel-left">
          <div class="panel-header">
            <span class="panel-title">{{ getVersionName(diffSourceVersion) }}</span>
            <span class="panel-subtitle">源版本</span>
          </div>
          <div class="panel-content">
            <!-- 新增记录（在源版本中存在） -->
            <div v-if="currentDiffCollection.added.length" class="diff-section">
              <div class="section-header added">
                <span class="section-icon">+</span>
                <span>新增记录</span>
                <span class="section-count">{{ currentDiffCollection.added.length }}</span>
              </div>
              <div class="record-list">
                <div v-for="record in currentDiffCollection.added" :key="record.id" class="record-card added">
                  <div class="record-id">{{ record.id }}</div>
                  <div class="record-name">{{ (record.name as string) || '—' }}</div>
                </div>
              </div>
            </div>
            <!-- 修改记录（源版本字段值） -->
            <div v-if="currentDiffCollection.modified.length" class="diff-section">
              <div class="section-header modified">
                <span class="section-icon">~</span>
                <span>修改记录</span>
                <span class="section-count">{{ currentDiffCollection.modified.length }}</span>
              </div>
              <div class="record-list">
                <div v-for="record in currentDiffCollection.modified" :key="record.id" class="record-card modified">
                  <div class="record-header">
                    <span class="record-id">{{ record.id }}</span>
                    <span class="record-name">{{ (record.record?.name as string) || (record.oldRecord?.name as string) || '—' }}</span>
                  </div>
                  <div class="field-changes">
                    <div v-for="field in record.fields" :key="field.fieldName" class="field-row source-value">
                      <span class="field-name">{{ field.fieldName }}</span>
                      <span class="field-value old">{{ field.oldValue || '—' }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <!-- 空状态 -->
            <div v-if="!currentDiffCollection.added.length && !currentDiffCollection.modified.length" class="panel-empty">
              源版本无变更数据
            </div>
          </div>
        </div>

        <!-- 中间分隔线 -->
        <div class="diff-divider">
          <span class="divider-arrow">→</span>
        </div>

        <!-- 右侧：目标版本 -->
        <div class="diff-panel diff-panel-right">
          <div class="panel-header">
            <span class="panel-title">{{ getVersionName(diffTargetVersion) }}</span>
            <span class="panel-subtitle">目标版本</span>
          </div>
          <div class="panel-content">
            <!-- 删除记录（在目标版本中不存在） -->
            <div v-if="currentDiffCollection.removed.length" class="diff-section">
              <div class="section-header removed">
                <span class="section-icon">-</span>
                <span>删除记录</span>
                <span class="section-count">{{ currentDiffCollection.removed.length }}</span>
              </div>
              <div class="record-list">
                <div v-for="record in currentDiffCollection.removed" :key="record.id" class="record-card removed">
                  <div class="record-id">{{ record.id }}</div>
                  <div class="record-name">{{ (record.name as string) || '—' }}</div>
                </div>
              </div>
            </div>
            <!-- 修改记录（目标版本字段值） -->
            <div v-if="currentDiffCollection.modified.length" class="diff-section">
              <div class="section-header modified">
                <span class="section-icon">~</span>
                <span>修改记录</span>
                <span class="section-count">{{ currentDiffCollection.modified.length }}</span>
              </div>
              <div class="record-list">
                <div v-for="record in currentDiffCollection.modified" :key="record.id" class="record-card modified">
                  <div class="record-header">
                    <span class="record-id">{{ record.id }}</span>
                    <span class="record-name">{{ (record.record?.name as string) || (record.oldRecord?.name as string) || '—' }}</span>
                  </div>
                  <div class="field-changes">
                    <div v-for="field in record.fields" :key="field.fieldName" class="field-row target-value">
                      <span class="field-name">{{ field.fieldName }}</span>
                      <span class="field-value new">{{ field.newValue || '—' }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <!-- 空状态 -->
            <div v-if="!currentDiffCollection.removed.length && !currentDiffCollection.modified.length" class="panel-empty">
              目标版本无变更数据
            </div>
          </div>
        </div>
      </div>

      <!-- 无变更状态 -->
      <div v-else-if="diffResult && !diffResult.collections.length" class="diff-empty">
        <el-empty description="两个版本完全一致，没有变更" />
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
import { Plus, Refresh, Loading } from '@element-plus/icons-vue'
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
import ProjectDependencyManager from './ProjectDependencyManager.vue'

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

const activeTab = ref('versions')
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
const selectedDiffCollection = ref<string>('')

const mergeDialogVisible = ref(false)
const mergeTarget = ref<ProjectVersion | null>(null)

// 当前选中的对比数据集
const currentDiffCollection = computed(() => {
  if (!diffResult.value || !selectedDiffCollection.value) return null
  return diffResult.value.collections.find(c => c.collection === selectedDiffCollection.value) || null
})

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

function getVersionName(versionId: string): string {
  if (!versionId) return ''
  if (versionId === 'main') return '主分支'
  if (versionId === 'current') return '当前分支'
  const version = versions.value.find(v => v.id === versionId)
  return version?.name || versionId
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
    // 对比版本：用户点击的版本 (source) vs 用户选择的对比版本 (target)
    const result = await diffProjectVersions(
      props.projectMenuId,
      diffSourceVersion.value,    // 基准版本：用户点击的那个版本
      diffTargetVersion.value     // 对比版本：用户选择的版本
    )
    diffResult.value = result
    // 默认选中第一个 collection
    if (result.collections.length > 0) {
      selectedDiffCollection.value = result.collections[0].collection
    }
  } catch (err: any) {
    ElMessage.error(err.message || '对比失败')
  } finally {
    diffLoading.value = false
  }
}

function handleMergeFromDiff() {
  // 从对比对话框跳转到合并对话框
  const sourceVersion = versions.value.find(v => v.id === diffSourceVersion.value)
  if (sourceVersion) {
    mergeTarget.value = sourceVersion
    mergeDialogVisible.value = true
  } else {
    ElMessage.warning('无法找到源版本信息')
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
      try {
        const result = await ElMessageBox.prompt(
          '请输入锁定原因（可选）',
          '锁定分支',
          {
            confirmButtonText: '锁定',
            cancelButtonText: '取消',
            inputPlaceholder: '例如：发布前冻结',
            type: 'warning',
          }
        ) as { value: string }
        await lockProjectVersion(version.id, result.value)
        ElMessage.success('分支已锁定')
      } catch {
        // 用户取消
        return
      }
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
      try {
        const result = await ElMessageBox.prompt(
          '请输入锁定原因（可选）',
          '锁定主分支',
          {
            confirmButtonText: '锁定',
            cancelButtonText: '取消',
            inputPlaceholder: '例如：发布前冻结',
            type: 'warning',
          }
        ) as { value: string }
        await lockMainBranch(props.projectMenuId, result.value)
        ElMessage.success('主分支已锁定')
      } catch {
        // 用户取消
        return
      }
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

// Beyond Compare 风格对比对话框样式
.diff-dialog-fullscreen {
  :deep(.el-dialog__body) {
    padding: 0;
    height: calc(100vh - 54px);
    overflow: hidden;
  }
}

.diff-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;

  .toolbar-left {
    display: flex;
    align-items: center;

    .version-label {
      font-weight: 500;
      color: #606266;
    }
  }
}

.diff-collection-tabs {
  padding: 12px 20px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;

  .el-radio-button {
    .diff-count {
      font-size: 12px;
      margin-left: 4px;

      &.added { color: #67c23a; }
      &.removed { color: #f56c6c; }
      &.modified { color: #e6a23c; }
    }
  }
}

.diff-stats-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 20px;
  background: #fafafa;
  border-bottom: 1px solid #e4e7ed;
  font-size: 14px;

  .stat-item {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .stat-value {
    font-weight: 600;
    color: #303133;
  }

  .stat-divider {
    color: #dcdfe6;
  }

  .stat-added { color: #67c23a; }
  .stat-removed { color: #f56c6c; }
  .stat-modified { color: #e6a23c; }
}

.diff-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 300px;
  gap: 12px;
  color: #909399;

  .is-loading {
    animation: rotating 2s linear infinite;
  }
}

@keyframes rotating {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.diff-compare-container {
  display: flex;
  height: calc(100vh - 180px);
  padding: 16px 20px;
  gap: 0;
}

.diff-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 1px solid #e4e7ed;
  background: #fff;

  &.diff-panel-left {
    border-radius: 4px 0 0 4px;
  }

  &.diff-panel-right {
    border-radius: 0 4px 4px 0;
    border-left: none;
  }

  .panel-header {
    display: flex;
    flex-direction: column;
    padding: 12px 16px;
    background: #f5f7fa;
    border-bottom: 1px solid #e4e7ed;

    .panel-title {
      font-weight: 600;
      font-size: 14px;
      color: #303133;
    }

    .panel-subtitle {
      font-size: 12px;
      color: #909399;
    }
  }

  .panel-content {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
  }

  .panel-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: #909399;
    font-size: 14px;
  }
}

.diff-divider {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  background: #fafafa;
  border-top: 1px solid #e4e7ed;
  border-bottom: 1px solid #e4e7ed;

  .divider-arrow {
    font-size: 18px;
    color: #909399;
  }
}

.diff-section {
  margin-bottom: 16px;

  .section-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-radius: 4px;
    font-weight: 500;
    font-size: 13px;

    .section-icon {
      width: 20px;
      height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 4px;
      font-weight: 600;
    }

    .section-count {
      color: #909399;
      font-size: 12px;
    }

    &.added {
      background: #f0f9eb;
      color: #67c23a;

      .section-icon { background: #67c23a; color: #fff; }
    }

    &.removed {
      background: #fef0f0;
      color: #f56c6c;

      .section-icon { background: #f56c6c; color: #fff; }
    }

    &.modified {
      background: #fdf6ec;
      color: #e6a23c;

      .section-icon { background: #e6a23c; color: #fff; }
    }
  }

  .record-list {
    margin-top: 8px;
  }
}

.record-card {
  padding: 12px 16px;
  margin-bottom: 8px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
  background: #fff;
  transition: all 0.2s;

  &:hover {
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  }

  .record-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
  }

  .record-id {
    font-size: 12px;
    color: #909399;
    font-family: monospace;
  }

  .record-name {
    font-weight: 500;
    color: #303133;
  }

  &.added {
    border-color: #c2e7b0;
    background: #f0f9eb;
  }

  &.removed {
    border-color: #fbc4c4;
    background: #fef0f0;
  }

  &.modified {
    border-color: #e6d5c3;
    background: #fdf6ec;
  }
}

.field-changes {
  .field-row {
    display: flex;
    align-items: center;
    padding: 4px 8px;
    border-radius: 2px;
    margin-bottom: 4px;

    .field-name {
      font-size: 12px;
      color: #606266;
      min-width: 100px;
    }

    .field-value {
      font-size: 12px;
      font-family: monospace;

      &.old { color: #f56c6c; background: #fef0f0; }
      &.new { color: #67c23a; background: #f0f9eb; }
    }
  }
}

.diff-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: calc(100vh - 200px);
}

.manager-tabs {
  :deep(.el-tabs__header) {
    margin-bottom: 16px;
  }
}
</style>