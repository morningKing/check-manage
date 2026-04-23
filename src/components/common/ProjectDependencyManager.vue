/**
 * 项目依赖管理组件
 *
 * 功能：
 * - 显示当前分支的依赖声明列表
 * - 创建/编辑/解除依赖声明
 * - 触发依赖校验
 * - 显示依赖校验结果和关联关系详情
 */
<template>
  <div class="project-dependency-manager">
    <!-- 标题栏 -->
    <div class="dependency-header">
      <div class="header-left">
        <span class="title">项目依赖管理</span>
        <el-tag v-if="currentBranch" :type="currentBranch.branchId === 'main' ? 'success' : 'warning'" size="small">
          {{ currentBranch.branchName }}
        </el-tag>
      </div>
      <div class="header-actions">
        <el-button type="primary" size="small" @click="showCreateDialog">
          <el-icon><Plus /></el-icon>
          添加依赖
        </el-button>
        <el-button size="small" @click="showDependentsDialog">
          查看被依赖
        </el-button>
        <el-button size="small" @click="refreshDependencies">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <!-- 依赖列表 -->
    <el-table v-loading="loading" :data="dependencies" stripe style="width: 100%">
      <el-table-column prop="targetProjectName" label="目标项目" min-width="150">
        <template #default="{ row }">
          <span>{{ row.targetProjectName || row.targetProject }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="targetBranch" label="目标分支" width="120">
        <template #default="{ row }">
          <el-tag :type="row.targetBranch === 'main' ? 'success' : 'warning'" size="small">
            {{ row.targetBranch === 'main' ? '主分支' : row.targetBranch }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="relationType" label="依赖类型" width="120">
        <template #default="{ row }">
          <el-tag :type="DEPENDENCY_TYPE_TAG_TYPES[row.relationType as DependencyRelationType]" size="small">
            {{ DEPENDENCY_TYPE_LABELS[row.relationType as DependencyRelationType] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="isValidated" label="校验状态" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.isValidated" type="success" size="small">
            <el-icon><Check /></el-icon> 已通过
          </el-tag>
          <el-tag v-else type="info" size="small">
            <el-icon><Clock /></el-icon> 待校验
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="declaredBy" label="声明者" width="120" />
      <el-table-column prop="declaredAt" label="声明时间" width="180">
        <template #default="{ row }">
          {{ formatDateTime(row.declaredAt) }}
        </template>
      </el-table-column>
      <el-table-column label="关联数" width="80">
        <template #default="{ row }">
          <el-tag type="info" size="small">
            {{ row.relations?.length || 0 }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link @click="showDependencyDetail(row)">详情</el-button>
          <el-button size="small" link type="primary" @click="handleValidate(row)">
            校验
          </el-button>
          <el-button size="small" link type="warning" @click="showEditDialog(row)">
            编辑
          </el-button>
          <el-button size="small" link type="danger" @click="handleDelete(row)">
            解除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空状态 -->
    <el-empty v-if="!loading && dependencies.length === 0" description="当前分支没有声明任何项目依赖" />

    <!-- 创建依赖对话框 -->
    <el-dialog v-model="createDialogVisible" title="添加项目依赖" width="500px" append-to-body>
      <el-form :model="createForm" label-width="100px">
        <el-form-item label="目标项目" required>
          <el-select v-model="createForm.targetProject" placeholder="选择要依赖的项目" style="width: 100%">
            <el-option v-for="p in availableProjects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标分支" required>
          <el-select v-model="createForm.targetBranch" placeholder="选择目标分支" style="width: 100%">
            <el-option label="主分支 (main)" value="main" />
            <el-option v-for="v in targetVersions" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="依赖类型" required>
          <el-radio-group v-model="createForm.relationType">
            <el-radio value="track-main">
              <el-tooltip content="自动接收目标main分支的数据更新" placement="top">
                <span>跟随主干</span>
              </el-tooltip>
            </el-radio>
            <el-radio value="read-write">
              <el-tooltip content="与目标分支配套开发，需要联合合并" placement="top">
                <span>配套分支</span>
              </el-tooltip>
            </el-radio>
            <el-radio value="read-only">
              <el-tooltip content="精确钉住特定版本，数据完全隔离" placement="top">
                <span>精确钉住</span>
              </el-tooltip>
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="createForm.relationType === 'read-only'" label="钉住版本">
          <el-select v-model="createForm.pinnedVersion" placeholder="选择要钉住的版本" style="width: 100%">
            <el-option v-for="v in targetVersions" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
        </el-form-item>
      </el-form>

      <!-- 关联关系预览 -->
      <div v-if="previewRelations.length > 0" class="relations-preview">
        <el-divider />
        <h4>检测到以下关联关系</h4>
        <el-table :data="previewRelations" stripe size="small">
          <el-table-column prop="sourceCollection" label="源数据集" width="150" />
          <el-table-column prop="sourceField" label="关联字段" width="150" />
          <el-table-column prop="targetCollection" label="目标数据集" width="150" />
          <el-table-column prop="controlType" label="类型" width="100" />
        </el-table>
      </div>
      <div v-else-if="createForm.targetProject && !previewLoading" class="relations-preview empty">
        <el-divider />
        <el-alert type="warning" :closable="false">
          未检测到与目标项目的关联关系，请确认项目间是否有数据引用
        </el-alert>
      </div>

      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" :disabled="!createForm.targetProject" @click="handleCreate">
          创建依赖
        </el-button>
      </template>
    </el-dialog>

    <!-- 编辑依赖对话框 -->
    <el-dialog v-model="editDialogVisible" title="编辑依赖声明" width="500px" append-to-body>
      <el-form :model="editForm" label-width="100px">
        <el-form-item label="目标分支">
          <el-select v-model="editForm.targetBranch" placeholder="选择目标分支" style="width: 100%">
            <el-option label="主分支 (main)" value="main" />
            <el-option v-for="v in targetVersions" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="依赖类型">
          <el-radio-group v-model="editForm.relationType">
            <el-radio value="track-main">跟随主干</el-radio>
            <el-radio value="read-write">配套分支</el-radio>
            <el-radio value="read-only">精确钉住</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="editForm.relationType === 'read-only'" label="钉住版本">
          <el-select v-model="editForm.pinnedVersion" placeholder="选择要钉住的版本" style="width: 100%">
            <el-option v-for="v in targetVersions" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="editing" @click="handleEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 依赖详情对话框 -->
    <el-dialog v-model="detailDialogVisible" title="依赖详情" width="700px" append-to-body>
      <el-descriptions v-if="detailData" :column="2" border>
        <el-descriptions-item label="目标项目">{{ detailData.targetProjectName }}</el-descriptions-item>
        <el-descriptions-item label="目标分支">
          <el-tag :type="detailData.targetBranch === 'main' ? 'success' : 'warning'" size="small">
            {{ detailData.targetBranch === 'main' ? '主分支' : detailData.targetBranch }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="依赖类型">
          <el-tag :type="DEPENDENCY_TYPE_TAG_TYPES[detailData.relationType]" size="small">
            {{ DEPENDENCY_TYPE_LABELS[detailData.relationType] }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="校验状态">
          <el-tag v-if="detailData.isValidated" type="success" size="small">已通过</el-tag>
          <el-tag v-else type="info" size="small">待校验</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="声明者">{{ detailData.declaredBy }}</el-descriptions-item>
        <el-descriptions-item label="声明时间">{{ formatDateTime(detailData.declaredAt) }}</el-descriptions-item>
        <el-descriptions-item v-if="detailData.validationError" label="校验错误" :span="2">
          <el-alert type="error" :closable="false">{{ detailData.validationError }}</el-alert>
        </el-descriptions-item>
      </el-descriptions>

      <!-- 关联关系详情 -->
      <div v-if="detailData?.relations?.length" class="relations-detail">
        <h4 style="margin-top: 16px">涉及的关联关系</h4>
        <el-table :data="detailData.relations" stripe size="small">
          <el-table-column prop="sourceCollection" label="源数据集" width="120" />
          <el-table-column prop="sourceField" label="关联字段" width="120" />
          <el-table-column prop="targetCollection" label="目标数据集" width="120" />
          <el-table-column prop="estimatedRecords" label="关联数" width="80" />
          <el-table-column prop="validationStatus" label="校验状态" width="100">
            <template #default="{ row }">
              <el-tag :type="VALIDATION_STATUS_TAG_TYPES[row.validationStatus as ValidationStatus]" size="small">
                {{ VALIDATION_STATUS_LABELS[row.validationStatus as ValidationStatus] }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="validationDetail" label="详情" />
        </el-table>
      </div>

      <template #footer>
        <el-button @click="detailDialogVisible = false">关闭</el-button>
        <el-button type="primary" :loading="validating" :disabled="!detailData" @click="handleValidate(detailData!)">
          重新校验
        </el-button>
      </template>
    </el-dialog>

    <!-- 被依赖列表（反向查询） -->
    <el-dialog v-model="dependentsDialogVisible" title="被依赖的项目" width="600px" append-to-body>
      <el-table v-loading="dependentsLoading" :data="dependents" stripe>
        <el-table-column prop="sourceProjectName" label="依赖方项目" />
        <el-table-column prop="sourceBranch" label="依赖方分支" width="120">
          <template #default="{ row }">
            <el-tag :type="row.sourceBranch === 'main' ? 'success' : 'warning'" size="small">
              {{ row.sourceBranch === 'main' ? '主分支' : row.sourceBranch }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="relationType" label="依赖类型" width="120">
          <template #default="{ row }">
            <el-tag :type="DEPENDENCY_TYPE_TAG_TYPES[row.relationType as DependencyRelationType]" size="small">
              {{ DEPENDENCY_TYPE_LABELS[row.relationType as DependencyRelationType] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="isValidated" label="校验状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.isValidated" type="success" size="small">已通过</el-tag>
            <el-tag v-else type="info" size="small">待校验</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!dependentsLoading && dependents.length === 0" description="没有项目依赖当前项目" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Check, Clock } from '@element-plus/icons-vue'
import {
  getProjectDependencies,
  createProjectDependency,
  updateProjectDependency,
  deleteProjectDependency,
  validateDependency,
  getDependentProjects,
  scanProjectRelations,
} from '@/api/crossProjectDependency'
import { listProjectVersions } from '@/api/projectVersion'
import { getMenuList } from '@/api/menu'
import {
  DEPENDENCY_TYPE_LABELS,
  DEPENDENCY_TYPE_TAG_TYPES,
  VALIDATION_STATUS_LABELS,
  VALIDATION_STATUS_TAG_TYPES,
} from '@/types/crossProjectDependency'
import type { ProjectDependency, DependencyRelation, DependencyRelationType, ValidationStatus } from '@/types/crossProjectDependency'
import type { ProjectVersion } from '@/types/version'

interface Props {
  projectMenuId: string
  currentBranch?: { branchId: string; branchName: string } | null
}

const props = defineProps<Props>()

const loading = ref(false)
const dependencies = ref<ProjectDependency[]>([])

const createDialogVisible = ref(false)
const createForm = ref<{
  sourceBranch: string
  targetProject: string
  targetBranch: string
  relationType: DependencyRelationType
  pinnedVersion: string
}>({
  sourceBranch: 'main',
  targetProject: '',
  targetBranch: 'main',
  relationType: 'track-main',
  pinnedVersion: '',
})
const creating = ref(false)
const previewRelations = ref<DependencyRelation[]>([])
const previewLoading = ref(false)

const editDialogVisible = ref(false)
const editForm = ref<{
  targetBranch: string
  relationType: DependencyRelationType
  pinnedVersion: string
}>({
  targetBranch: '',
  relationType: 'track-main',
  pinnedVersion: '',
})
const editing = ref(false)
const editingDependency = ref<ProjectDependency | null>(null)

const detailDialogVisible = ref(false)
const detailData = ref<ProjectDependency | null>(null)
const validating = ref(false)

const dependentsDialogVisible = ref(false)
const dependents = ref<ProjectDependency[]>([])
const dependentsLoading = ref(false)

const availableProjects = ref<{ id: string; name: string }[]>([])
const targetVersions = ref<ProjectVersion[]>([])

function formatDateTime(dateStr?: string): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN')
}

async function refreshDependencies() {
  loading.value = true
  try {
    const branchId = props.currentBranch?.branchId
    const result = await getProjectDependencies(props.projectMenuId, branchId)
    dependencies.value = result.dependencies
  } catch (err: any) {
    ElMessage.error(err.message || '获取依赖列表失败')
  } finally {
    loading.value = false
  }
}

async function loadAvailableProjects() {
  try {
    const menus = await getMenuList()
    const projects = menus.filter((m: any) => m.menuType === 'project' && m.id !== props.projectMenuId)
    availableProjects.value = projects.map((p: any) => ({ id: p.id, name: p.name }))
  } catch (err: any) {
    ElMessage.error(err.message || '获取项目列表失败')
  }
}

async function loadTargetVersions(projectId: string) {
  try {
    const result = await listProjectVersions(projectId, 1, 100)
    targetVersions.value = result.items.filter((v: ProjectVersion) => v.status === 'active')
  } catch (err: any) {
    ElMessage.error(err.message || '获取版本列表失败')
  }
}

async function showCreateDialog() {
  createForm.value = {
    sourceBranch: props.currentBranch?.branchId || 'main',
    targetProject: '',
    targetBranch: 'main',
    relationType: 'track-main',
    pinnedVersion: '',
  }
  previewRelations.value = []
  await loadAvailableProjects()
  createDialogVisible.value = true
}

async function handleTargetProjectChange() {
  if (!createForm.value.targetProject) {
    previewRelations.value = []
    return
  }

  previewLoading.value = true
  try {
    await loadTargetVersions(createForm.value.targetProject)
    const result = await scanProjectRelations(props.projectMenuId, createForm.value.targetProject)
    previewRelations.value = result.relations.map((r: any) => ({
      ...r,
      controlType: r.controlType,
    })) as DependencyRelation[]
  } catch (err: any) {
    ElMessage.error(err.message || '扫描关联关系失败')
  } finally {
    previewLoading.value = false
  }
}

async function handleCreate() {
  if (!createForm.value.targetProject) {
    ElMessage.warning('请选择目标项目')
    return
  }

  creating.value = true
  try {
    await createProjectDependency(props.projectMenuId, {
      sourceBranch: createForm.value.sourceBranch,
      targetProject: createForm.value.targetProject,
      targetBranch: createForm.value.targetBranch,
      relationType: createForm.value.relationType,
      pinnedVersion: createForm.value.pinnedVersion || undefined,
    })
    ElMessage.success('依赖声明创建成功')
    createDialogVisible.value = false
    refreshDependencies()
  } catch (err: any) {
    ElMessage.error(err.response?.data?.error || err.message || '创建失败')
  } finally {
    creating.value = false
  }
}

function showEditDialog(dep: ProjectDependency) {
  editingDependency.value = dep
  editForm.value = {
    targetBranch: dep.targetBranch,
    relationType: dep.relationType,
    pinnedVersion: dep.pinnedVersion || '',
  }
  loadTargetVersions(dep.targetProject)
  editDialogVisible.value = true
}

async function handleEdit() {
  if (!editingDependency.value) return

  editing.value = true
  try {
    await updateProjectDependency(props.projectMenuId, editingDependency.value.id, {
      targetBranch: editForm.value.targetBranch,
      relationType: editForm.value.relationType,
      pinnedVersion: editForm.value.pinnedVersion || undefined,
    })
    ElMessage.success('依赖声明更新成功')
    editDialogVisible.value = false
    refreshDependencies()
  } catch (err: any) {
    ElMessage.error(err.response?.data?.error || err.message || '更新失败')
  } finally {
    editing.value = false
  }
}

function showDependencyDetail(dep: ProjectDependency) {
  detailData.value = dep
  detailDialogVisible.value = true
}

async function handleValidate(dep: ProjectDependency) {
  if (!dep) return

  validating.value = true
  try {
    const result = await validateDependency(dep.id)
    if (result.isValid) {
      ElMessage.success('依赖校验通过')
    } else {
      ElMessage.warning(`校验发现问题: ${result.errors.join(', ')}`)
    }
    refreshDependencies()
    if (detailDialogVisible.value && detailData.value?.id === dep.id) {
      detailData.value = await getProjectDependencies(props.projectMenuId, props.currentBranch?.branchId)
        .then(r => r.dependencies.find(d => d.id === dep.id) || dep)
    }
  } catch (err: any) {
    ElMessage.error(err.response?.data?.error || err.message || '校验失败')
  } finally {
    validating.value = false
  }
}

async function handleDelete(dep: ProjectDependency) {
  try {
    await ElMessageBox.confirm(
      `确定解除对项目「${dep.targetProjectName}」的依赖？`,
      '解除依赖',
      { type: 'warning' }
    )

    await deleteProjectDependency(props.projectMenuId, dep.id)
    ElMessage.success('依赖已解除')
    refreshDependencies()
  } catch (err: any) {
    if (err !== 'cancel') {
      ElMessage.error(err.response?.data?.error || err.message || '解除失败')
    }
  }
}

async function showDependentsDialog() {
  dependentsDialogVisible.value = true
  dependentsLoading.value = true
  try {
    const result = await getDependentProjects(props.projectMenuId)
    dependents.value = result.dependents
  } catch (err: any) {
    ElMessage.error(err.message || '获取被依赖列表失败')
  } finally {
    dependentsLoading.value = false
  }
}

// 监听目标项目变化，自动扫描关联关系
watch(() => createForm.value.targetProject, () => {
  handleTargetProjectChange()
})

// 监听当前分支变化，刷新依赖列表
watch(() => props.currentBranch?.branchId, () => {
  refreshDependencies()
})

onMounted(() => {
  refreshDependencies()
})
</script>

<style scoped lang="scss">
.project-dependency-manager {
  .dependency-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;

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

  .relations-preview {
    h4 {
      margin-bottom: 12px;
      color: #606266;
    }
  }
}
</style>