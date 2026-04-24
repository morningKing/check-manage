/**
 * 项目依赖管理页面
 *
 * 职责：
 * - 显示项目依赖声明列表
 * - 支持创建、编辑、删除依赖声明
 * - 显示依赖校验状态和通知
 */
<template>
  <div class="dependency-manager">
    <div class="page-header">
      <h2>项目依赖管理</h2>
      <el-button type="primary" @click="handleAdd">新增依赖</el-button>
    </div>

    <!-- 项目选择器 -->
    <el-select
      v-model="selectedProject"
      placeholder="选择项目"
      filterable
      style="width: 300px; margin-bottom: 16px"
      @change="loadDependencies"
    >
      <el-option
        v-for="project in projects"
        :key="project.id"
        :label="project.name"
        :value="project.id"
      />
    </el-select>

    <!-- 依赖列表 -->
    <el-table :data="dependencies" v-loading="loading" border stripe>
      <el-table-column prop="targetProjectName" label="目标项目" width="150" />
      <el-table-column prop="targetBranch" label="目标分支" width="150">
        <template #default="{ row }">
          <el-tag :type="row.targetBranch === 'main' ? 'success' : 'warning'" size="small">
            {{ row.targetBranch }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="relationType" label="依赖类型" width="120">
        <template #default="{ row }">
          <el-tag :type="getRelationTypeTag(row.relationType)" size="small">
            {{ getRelationTypeLabel(row.relationType) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="isValidated" label="校验状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.isValidated ? 'success' : 'danger'" size="small">
            {{ row.isValidated ? '有效' : '无效' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="validationError" label="校验错误" min-width="200">
        <template #default="{ row }">
          <span v-if="row.validationError" class="error-text">{{ row.validationError }}</span>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button link @click="handleEdit(row)">编辑</el-button>
          <el-button link @click="handleValidate(row)">校验</el-button>
          <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editVisible" :title="editForm.id ? '编辑依赖' : '新增依赖'" width="500px">
      <el-form ref="formRef" :model="editForm" :rules="formRules" label-width="100px">
        <el-form-item label="目标项目" prop="targetProject">
          <el-select v-model="editForm.targetProject" filterable placeholder="选择目标项目">
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标分支" prop="targetBranch">
          <el-select v-model="editForm.targetBranch" placeholder="选择目标分支">
            <el-option label="main" value="main" />
            <el-option v-for="v in targetVersions" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="依赖类型" prop="relationType">
          <el-select v-model="editForm.relationType" placeholder="选择依赖类型">
            <el-option label="跟随主干 (track-main)" value="track-main" />
            <el-option label="配套分支 (read-write)" value="read-write" />
            <el-option label="精确钉住 (read-only)" value="read-only" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editForm.relationType === 'read-only'" label="钉住版本">
          <el-select v-model="editForm.pinnedVersion" placeholder="选择钉住版本">
            <el-option v-for="v in targetVersions" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed, watch } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { useRoute } from 'vue-router'
import {
  getProjectDependencies,
  createProjectDependency,
  updateProjectDependency,
  deleteProjectDependency,
  validateDependency,
  type CreateDependencyRequest,
  type UpdateDependencyRequest,
} from '@/api/crossProjectDependency'
import { getMenuList } from '@/api/menu'
import { listProjectVersions } from '@/api/projectVersion'
import type { ProjectDependency, DependencyRelationType } from '@/types/crossProjectDependency'
import type { ProjectVersion } from '@/types/version'

const route = useRoute()

const loading = ref(false)
const saving = ref(false)
const dependencies = ref<ProjectDependency[]>([])
const projects = ref<{ id: string; name: string }[]>([])
const targetVersions = ref<ProjectVersion[]>([])
const editVisible = ref(false)
const formRef = ref<FormInstance>()

// 从路由参数或选择器获取项目ID
const routeProjectId = computed(() => route.query.projectId as string || route.params.projectId as string)
const selectedProject = ref<string>('')

const editForm = reactive({
  id: '',
  sourceProject: '',
  sourceBranch: 'main',
  targetProject: '',
  targetBranch: 'main',
  relationType: 'track-main',
  pinnedVersion: '',
})

const formRules: FormRules = {
  targetProject: [{ required: true, message: '请选择目标项目', trigger: 'change' }],
  targetBranch: [{ required: true, message: '请选择目标分支', trigger: 'change' }],
  relationType: [{ required: true, message: '请选择依赖类型', trigger: 'change' }],
}

function getRelationTypeTag(type: string): string {
  switch (type) {
    case 'track-main': return 'success'
    case 'read-write': return 'warning'
    case 'read-only': return 'info'
    default: return ''
  }
}

function getRelationTypeLabel(type: string): string {
  switch (type) {
    case 'track-main': return '跟随主干'
    case 'read-write': return '配套分支'
    case 'read-only': return '精确钉住'
    default: return type
  }
}

async function loadProjects() {
  try {
    const menus = await getMenuList()
    // 只获取项目类型的菜单
    projects.value = menus
      .filter((m: any) => m.menuType === 'folder' && m.id.startsWith('test-project') || m.menuType === 'project')
      .map((m: any) => ({ id: m.id, name: m.name }))

    // 如果路由中有项目ID，自动选择
    if (routeProjectId.value) {
      selectedProject.value = routeProjectId.value
      loadDependencies()
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '加载项目列表失败')
  }
}

async function loadDependencies() {
  if (!selectedProject.value) return
  loading.value = true
  try {
    const result = await getProjectDependencies(selectedProject.value)
    dependencies.value = result.dependencies
  } catch (e: any) {
    ElMessage.error(e?.message || '加载依赖列表失败')
  } finally {
    loading.value = false
  }
}

async function loadTargetVersions(projectId: string) {
  try {
    const result = await listProjectVersions(projectId, 1, 100)
    targetVersions.value = result.items.filter((v: ProjectVersion) => v.status === 'active' || v.status === 'merged')
  } catch (e: any) {
    targetVersions.value = []
  }
}

function handleAdd() {
  if (!selectedProject.value) {
    ElMessage.warning('请先选择项目')
    return
  }
  Object.assign(editForm, {
    id: '',
    sourceProject: selectedProject.value,
    sourceBranch: 'main',
    targetProject: '',
    targetBranch: 'main',
    relationType: 'track-main',
    pinnedVersion: '',
  })
  editVisible.value = true
}

function handleEdit(row: ProjectDependency) {
  Object.assign(editForm, {
    id: row.id,
    sourceProject: selectedProject.value,
    sourceBranch: row.sourceBranch,
    targetProject: row.targetProject,
    targetBranch: row.targetBranch,
    relationType: row.relationType,
    pinnedVersion: row.pinnedVersion || '',
  })
  loadTargetVersions(row.targetProject)
  editVisible.value = true
}

watch(() => editForm.targetProject, (projectId) => {
  if (projectId && editVisible.value) {
    loadTargetVersions(projectId)
  }
})

async function handleSave() {
  if (!formRef.value) return
  await formRef.value.validate()

  saving.value = true
  try {
    if (editForm.id) {
      const payload: UpdateDependencyRequest = {
        targetBranch: editForm.targetBranch,
        relationType: editForm.relationType as DependencyRelationType,
        pinnedVersion: editForm.pinnedVersion || undefined,
      }
      await updateProjectDependency(selectedProject.value, editForm.id, payload)
    } else {
      const payload: CreateDependencyRequest = {
        sourceBranch: editForm.sourceBranch,
        targetProject: editForm.targetProject,
        targetBranch: editForm.targetBranch,
        relationType: editForm.relationType as DependencyRelationType,
        pinnedVersion: editForm.pinnedVersion || undefined,
      }
      await createProjectDependency(selectedProject.value, payload)
    }

    editVisible.value = false
    ElMessage.success('保存成功')
    await loadDependencies()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleValidate(row: ProjectDependency) {
  try {
    const result = await validateDependency(row.id)
    if (result.isValid) {
      ElMessage.success('校验通过')
    } else {
      ElMessage.warning(`校验失败: ${result.errors?.join(', ')}`)
    }
    await loadDependencies()
  } catch (e: any) {
    ElMessage.error(e?.message || '校验失败')
  }
}

async function handleDelete(row: ProjectDependency) {
  try {
    await ElMessageBox.confirm(`确定删除此依赖声明？`, '确认删除')
    await deleteProjectDependency(selectedProject.value, row.id)
    ElMessage.success('已删除')
    await loadDependencies()
  } catch {
    // cancelled
  }
}

onMounted(() => {
  loadProjects()
})
</script>

<style scoped>
.dependency-manager {
  padding: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.page-header h2 {
  margin: 0;
}

.error-text {
  color: #f56c6c;
}
</style>