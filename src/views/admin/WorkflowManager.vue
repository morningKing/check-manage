<template>
  <div class="workflow-manager">
    <div class="page-header">
      <h2>工作流</h2>
      <el-button type="primary" @click="goCreate">新建工作流</el-button>
    </div>

    <el-table :data="store.definitions" border stripe v-loading="store.loading">
      <el-table-column prop="name" label="名称" min-width="180" />
      <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
      <el-table-column label="阶段数" width="90" align="center">
        <template #default="{ row }">{{ row.stages.length }}</template>
      </el-table-column>
      <el-table-column label="启用" width="90" align="center">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="(v: boolean) => handleToggle(row, v)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button link @click="goEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useWorkflowStore } from '@/stores/workflow'
import type { WorkflowDefinition } from '@/types/workflow'

const store = useWorkflowStore()
const router = useRouter()

function goCreate() {
  router.push('/workflow/new')
}

function goEdit(row: WorkflowDefinition) {
  router.push(`/workflow/${row.id}`)
}

async function handleToggle(row: WorkflowDefinition, enabled: boolean) {
  try {
    await store.save({ ...row, enabled })
  } catch {
    /* 全局拦截器已提示 */
  }
}

async function handleDelete(row: WorkflowDefinition) {
  if (!row.id) return
  try {
    await ElMessageBox.confirm(`确定删除工作流「${row.name}」？`, '确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await store.remove(row.id)
    ElMessage.success('已删除')
  } catch {
    /* 全局拦截器已提示 */
  }
}

onMounted(async () => {
  await store.loadDefinitions()
})
</script>

<style scoped lang="scss">
.workflow-manager {
  padding: 0;
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
</style>
