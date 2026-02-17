<template>
  <div class="api-key-manager">
    <el-card>
      <template #header>
        <div class="card-header">
          <h2>Open API 密钥管理</h2>
          <el-button type="primary" @click="handleAdd">
            <el-icon><Plus /></el-icon>
            创建密钥
          </el-button>
        </div>
      </template>

      <el-alert
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 16px"
      >
        <template #title>
          外部系统通过 <strong>X-API-Key</strong> 请求头携带密钥访问
          <code>GET /api/v1/collections</code> 等接口读取数据。
          需在「页面配置」中开启 Open API 开关的数据页才可被访问。
        </template>
      </el-alert>

      <el-table :data="keyList" v-loading="loading" stripe border style="width: 100%">
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="createdAt" label="创建时间" width="180">
          <template #default="{ row }">{{ formatDate(row.createdAt) }}</template>
        </el-table-column>
        <el-table-column prop="lastUsedAt" label="最后使用" width="180">
          <template #default="{ row }">{{ row.lastUsedAt ? formatDate(row.lastUsedAt) : '从未使用' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.isActive ? 'success' : 'info'" size="small">
              {{ row.isActive ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="160" align="center">
          <template #default="{ row }">
            <el-button type="warning" link size="small" @click="handleToggle(row)">
              {{ row.isActive ? '停用' : '启用' }}
            </el-button>
            <el-button type="danger" link size="small" @click="handleDeleteConfirm(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建密钥对话框 -->
    <el-dialog
      v-model="createDialogVisible"
      title="创建 API 密钥"
      width="450px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form ref="formRef" :model="formData" :rules="formRules" label-width="80px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="formData.name" placeholder="如：数据同步系统" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false" :disabled="createLoading">取消</el-button>
        <el-button type="primary" :loading="createLoading" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 密钥展示对话框（仅创建后显示一次） -->
    <el-dialog
      v-model="keyRevealVisible"
      title="密钥已创建"
      width="550px"
      :close-on-click-modal="false"
    >
      <el-alert
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 16px"
      >
        <template #title>
          请立即复制并妥善保存此密钥，关闭后将无法再次查看。
        </template>
      </el-alert>
      <el-input
        :model-value="revealedKey"
        readonly
        style="font-family: monospace"
      >
        <template #append>
          <el-button @click="handleCopyKey">复制</el-button>
        </template>
      </el-input>
      <template #footer>
        <el-button type="primary" @click="keyRevealVisible = false">我已保存</el-button>
      </template>
    </el-dialog>

    <!-- 删除确认 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      :message="`确定要删除密钥「${deleteTarget?.name}」吗？删除后使用此密钥的外部系统将无法访问。`"
      type="danger"
      confirm-text="删除"
      @confirm="handleDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import { getApiKeyList, createApiKey, toggleApiKey, deleteApiKey } from '@/api/apiKey'
import { ConfirmDialog } from '@/components/common'
import type { ApiKey } from '@/types'

const loading = ref(false)
const keyList = ref<ApiKey[]>([])

const createDialogVisible = ref(false)
const createLoading = ref(false)
const formRef = ref<FormInstance>()
const formData = ref({ name: '' })
const formRules: FormRules = {
  name: [{ required: true, message: '请输入密钥名称', trigger: 'blur' }],
}

const keyRevealVisible = ref(false)
const revealedKey = ref('')

const deleteDialogVisible = ref(false)
const deleteTarget = ref<ApiKey | null>(null)

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
    return `${y}-${m}-${d} ${h}:${min}`
  } catch {
    return value
  }
}

async function loadKeys(): Promise<void> {
  loading.value = true
  try {
    keyList.value = await getApiKeyList()
  } catch {
    ElMessage.error('加载密钥列表失败')
  } finally {
    loading.value = false
  }
}

function handleAdd(): void {
  formData.value = { name: '' }
  createDialogVisible.value = true
}

async function handleCreate(): Promise<void> {
  const valid = await formRef.value?.validate()
  if (!valid) return

  createLoading.value = true
  try {
    const result = await createApiKey({ name: formData.value.name })
    createDialogVisible.value = false
    revealedKey.value = result.key || ''
    keyRevealVisible.value = true
    await loadKeys()
  } catch {
    ElMessage.error('创建失败')
  } finally {
    createLoading.value = false
  }
}

function handleCopyKey(): void {
  navigator.clipboard.writeText(revealedKey.value).then(() => {
    ElMessage.success('已复制到剪贴板')
  }).catch(() => {
    ElMessage.warning('复制失败，请手动选择复制')
  })
}

async function handleToggle(row: ApiKey): Promise<void> {
  try {
    await toggleApiKey(row.id, !row.isActive)
    ElMessage.success(row.isActive ? '已停用' : '已启用')
    await loadKeys()
  } catch {
    ElMessage.error('操作失败')
  }
}

function handleDeleteConfirm(row: ApiKey): void {
  deleteTarget.value = row
  deleteDialogVisible.value = true
}

async function handleDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deleteApiKey(deleteTarget.value.id)
    ElMessage.success('删除成功')
    deleteDialogVisible.value = false
    await loadKeys()
  } catch {
    ElMessage.error('删除失败')
  }
}

onMounted(() => {
  loadKeys()
})
</script>

<style scoped lang="scss">
.api-key-manager {
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
</style>
