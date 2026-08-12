<template>
  <div class="batch-admin">
    <div class="batch-admin__filters">
      <el-select v-model="store.filters.status" placeholder="状态" clearable style="width: 130px">
        <el-option v-for="s in STATUSES" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-select v-model="store.filters.source" placeholder="来源" clearable style="width: 120px">
        <el-option label="界面" value="ui" />
        <el-option label="API" value="api" />
      </el-select>
      <el-input v-model="store.filters.owner" placeholder="归属用户" clearable style="width: 160px" />
      <el-input v-model="store.filters.keyword" placeholder="批任务名称" clearable style="width: 200px" />
      <el-button type="primary" @click="reload">查询</el-button>
      <el-button :loading="store.loading" @click="reload">刷新</el-button>
    </div>

    <el-table :data="store.items" v-loading="store.loading" style="width: 100%">
      <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
      <el-table-column prop="ownerUsername" label="归属用户" width="140" />
      <el-table-column label="来源" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.source === 'api' ? 'warning' : 'info'">
            {{ row.source === 'api' ? 'API' : '界面' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度" width="140">
        <template #default="{ row }">
          成功 {{ row.done }} / 失败 {{ row.failed }} / 共 {{ row.total }}
        </template>
      </el-table-column>
      <el-table-column label="Agent·模型" min-width="160">
        <template #default="{ row }">{{ row.agent || '默认' }} · {{ row.model || '默认' }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ fmt(row.createdAt) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination class="batch-admin__pager" background layout="total, prev, pager, next"
                   :total="store.total" :current-page="store.page" :page-size="store.pageSize"
                   @current-change="onPage" />

    <el-drawer v-model="detailOpen" :title="detail?.batch?.name || '批任务详情'" size="60%">
      <div v-if="detail" class="batch-admin__detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="归属用户">{{ detail.batch.ownerUsername }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ detail.batch.source === 'api' ? 'API' : '界面' }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusLabel(detail.batch.status) }}</el-descriptions-item>
          <el-descriptions-item label="进度">
            成功 {{ detail.batch.done }} / 失败 {{ detail.batch.failed }} / 共 {{ detail.batch.total }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="batch-admin__actions">
          <el-button type="warning" :disabled="!detail.batch.failed" :loading="retrying"
                     @click="onRetryAll">重试全部失败（{{ detail.batch.failed }}）</el-button>
        </div>

        <el-table :data="detail.sessions" size="small" style="width: 100%">
          <el-table-column prop="seq" label="#" width="60" />
          <el-table-column prop="name" label="文件" min-width="160" show-overflow-tooltip />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="error" label="错误信息" min-width="220" show-overflow-tooltip />
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openConversation(row)">查看对话</el-button>
              <el-button link type="warning" :disabled="!isTerminal(row.status)"
                         @click="onReexecute(row)">重跑</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>

    <el-dialog v-model="convOpen" :title="`子任务对话：${convName}`" width="70%" top="6vh">
      <BatchConversationView :messages="convMessages" :truncated="convTruncated"
                             :total="convTotal" :loading="convLoading" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAiBatchAdminStore } from '@/stores/aiBatchAdmin'
import BatchConversationView from '@/components/ai-chat/BatchConversationView.vue'
import {
  getAdminBatch, getAdminChildMessages, retryAdminBatch, reexecuteAdminChild,
  type AdminBatch, type AdminChild, type AdminMessage,
} from '@/api/aiBatchAdmin'

const STATUSES = [
  { value: 'pending', label: '待处理' },
  { value: 'running', label: '执行中' },
  { value: 'completed', label: '已完成' },
  { value: 'partial', label: '部分完成' },
  { value: 'failed', label: '失败' },
]
const LABELS: Record<string, string> = Object.fromEntries(
  STATUSES.map(s => [s.value, s.label]))

const store = useAiBatchAdminStore()
const detailOpen = ref(false)
const detail = ref<{ batch: AdminBatch; sessions: AdminChild[] } | null>(null)
const retrying = ref(false)
const convOpen = ref(false)
const convName = ref('')
const convMessages = ref<AdminMessage[]>([])
const convTruncated = ref(false)
const convTotal = ref(0)
const convLoading = ref(false)

function statusLabel(s: string) { return LABELS[s] || s }
function statusTagType(s: string) {
  if (s === 'failed') return 'danger'
  if (s === 'completed') return 'success'
  if (s === 'partial') return 'warning'
  if (s === 'running') return 'primary'
  return 'info'
}
function isTerminal(s: string) { return s === 'completed' || s === 'failed' }
function fmt(v: string | null) { return v ? new Date(v).toLocaleString('zh-CN') : '-' }

async function reload() {
  store.page = 1
  await store.fetchList()
  store.startPolling()
}

async function onPage(p: number) {
  store.page = p
  await store.fetchList()
  store.startPolling()
}

async function openDetail(row: AdminBatch) {
  detail.value = await getAdminBatch(row.batchId)
  detailOpen.value = true
}

async function onRetryAll() {
  if (!detail.value) return
  retrying.value = true
  try {
    const res = await retryAdminBatch(detail.value.batch.batchId)
    ElMessage.success(`已重置 ${res.retried} 个失败的子任务`)
    detail.value = await getAdminBatch(detail.value.batch.batchId)
    await store.fetchList()
    store.startPolling()
  } finally {
    retrying.value = false
  }
}

async function onReexecute(row: AdminChild) {
  if (!detail.value) return
  await reexecuteAdminChild(detail.value.batch.batchId, row.sessionId)
  ElMessage.success('已提交重跑')
  detail.value = await getAdminBatch(detail.value.batch.batchId)
  await store.fetchList()
  store.startPolling()
}

async function openConversation(row: AdminChild) {
  if (!detail.value) return
  convName.value = row.name
  convOpen.value = true
  convLoading.value = true
  try {
    const res = await getAdminChildMessages(detail.value.batch.batchId, row.sessionId)
    convMessages.value = res.messages
    convTruncated.value = res.truncated
    convTotal.value = res.total
  } finally {
    convLoading.value = false
  }
}

onMounted(reload)
onUnmounted(() => store.stopPolling())
</script>

<style scoped lang="scss">
.batch-admin__filters { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.batch-admin__pager { margin-top: 12px; justify-content: flex-end; }
.batch-admin__actions { margin: 12px 0; }
</style>
