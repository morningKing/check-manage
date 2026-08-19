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

    <el-alert v-if="store.pollError" type="warning" show-icon :closable="false"
              class="batch-admin__poll-error"
              title="后台自动刷新已连续多次失败并已停止，当前列表可能不是最新数据">
      <el-button size="small" type="warning" plain @click="reload">重新加载</el-button>
    </el-alert>

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
          <el-table-column label="操作" width="230" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openConversation(row)">查看对话</el-button>
              <el-button link type="primary" @click="openChildFiles(row)">产出文件</el-button>
              <el-button link type="warning" :disabled="!isTerminal(row.status)"
                         @click="onReexecute(row)">重跑</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>

    <el-dialog v-model="convOpen" :title="`子任务对话：${convName}`" width="70%" top="6vh">
      <BatchConversationView :messages="convMessages" :truncated="convTruncated"
                             :total="convTotal" :loading="convLoading"
                             :session-id="convSessionId" :fetch-subtask-fn="fetchSubtaskForConv" />
    </el-dialog>

    <el-dialog v-model="filesOpen" :title="`产出文件：${filesIdent}`" width="70%" top="6vh">
      <AdminBatchFiles
        v-if="filesOpen"
        :batch-id="filesBatchId"
        :session-id="filesSessionId"
        :files="childFiles"
        :loading="childFilesLoading"
        :truncated="childFilesTruncated"
        :ident="filesIdent"
        @preview="onPreviewChildFile"
        @import="onImportChildFiles"
      />
    </el-dialog>

    <el-dialog v-model="previewOpen" :title="`预览：${previewPath}`" width="60%" top="10vh" append-to-body>
      <div v-if="previewLoading" class="admin-preview__hint">加载中…</div>
      <template v-else>
        <pre class="admin-preview__text">{{ previewContent }}</pre>
        <el-alert v-if="previewTruncated" type="info" :closable="false"
                  title="内容过长，已截断显示" />
      </template>
    </el-dialog>

    <!-- Word/Excel/PPT/PDF 预览；append-to-body 同上，避免被 filesOpen 的 mask 盖住 -->
    <FilePreviewDialog v-model="officePreviewVisible" :file="officePreviewFile" append-to-body />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, defineAsyncComponent } from 'vue'
import { ElMessage } from 'element-plus'
import { useAiBatchAdminStore } from '@/stores/aiBatchAdmin'
import BatchConversationView from '@/components/ai-chat/BatchConversationView.vue'
import AdminBatchFiles from '@/components/ai-chat/AdminBatchFiles.vue'
import { previewKind } from '@/utils/filePreview'
import {
  getAdminBatch, getAdminChildMessages, retryAdminBatch, reexecuteAdminChild,
  getAdminSubtaskMessages,
  listAdminChildFiles, importAdminChildFiles, getAdminChildFilePreview,
  adminChildFileDownloadUrl,
  type AdminBatch, type AdminChild, type AdminMessage,
  type AdminChildFile, type AdminImportResult,
} from '@/api/aiBatchAdmin'

// 懒加载：Word/Excel/PPT/PDF 预览用 @vue-office/*，跟 DynamicPage.vue 同样的
// 顾虑——避免这些重型库进这个页面的主 chunk。
const FilePreviewDialog = defineAsyncComponent(() => import('@/components/common/FilePreviewDialog.vue'))

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
const convSessionId = ref('')
const convMessages = ref<AdminMessage[]>([])
const convTruncated = ref(false)
const convTotal = ref(0)
const convLoading = ref(false)

// BatchConversationView 递归渲染子代理气泡时惰性拉取用——绑定当前批任务 id,
// 因为管理员视角走的是 /ai/chat/admin/batches 这套鉴权端点,不是普通用户端点。
function fetchSubtaskForConv(sessionId: string, subtaskId: string) {
  return getAdminSubtaskMessages(detail.value!.batch.batchId, sessionId, subtaskId)
}

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
  try {
    detail.value = await getAdminBatch(row.batchId)
    detailOpen.value = true
  } catch (e: any) {
    // 所有者在列表渲染与点击之间删掉了该批任务：全局拦截器已弹出「请求资源不存在」，
    // 这里只需把陈旧行从列表里清掉，不然它会一直留在表格里，再点还是 404。
    if (e?.response?.status === 404) {
      await store.fetchList()
    }
  }
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
  convSessionId.value = row.sessionId
  // 先清空再开弹窗：否则连续点击两个不同子任务时，新标题下会短暂（或请求失败时永久）
  // 显示上一个子任务的旧对话内容。
  convMessages.value = []
  convTruncated.value = false
  convTotal.value = 0
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

// 产出文件 dialog —— 同 convOpen 风格的局部 state（不进 store）。先清空再开弹窗，
// 这样切换子任务时不会泄露上一行残留列表；列表为空时 AdminBatchFiles 显示
// "加载中…" loading 占位，请求完成后再填充。
const filesOpen = ref(false)
const filesBatchId = ref('')
const filesSessionId = ref('')
const filesIdent = ref('')
const childFiles = ref<AdminChildFile[]>([])
const childFilesLoading = ref(false)
const childFilesTruncated = ref(false)

// 预览嵌套 dialog：append-to-body 让它脱离 filesOpen 的 z-index 上下文，
// 否则会被父 dialog 的 mask 盖住。
const previewOpen = ref(false)
const previewPath = ref('')
const previewContent = ref('')
const previewTruncated = ref(false)
const previewLoading = ref(false)

// Word/Excel/PPT/PDF 不是能有意义地当纯文本展示的格式，改走 FilePreviewDialog
// 的 @vue-office 渲染器；同样 append-to-body 避免被 filesOpen 的 mask 盖住。
const officePreviewVisible = ref(false)
const officePreviewFile = ref<{ name: string; url: string } | null>(null)

async function openChildFiles(row: AdminChild) {
  if (!detail.value) return
  filesBatchId.value = detail.value.batch.batchId
  filesSessionId.value = row.sessionId
  filesIdent.value = `${row.name} #${row.seq}`
  childFiles.value = []
  childFilesTruncated.value = false
  childFilesLoading.value = true
  filesOpen.value = true
  try {
    const res = await listAdminChildFiles(filesBatchId.value, filesSessionId.value)
    childFiles.value = res.files
    childFilesTruncated.value = res.truncated
  } finally {
    childFilesLoading.value = false
  }
}

async function onPreviewChildFile(f: AdminChildFile) {
  if (['docx', 'excel', 'pptx', 'pdf'].includes(previewKind(f.name))) {
    officePreviewFile.value = {
      name: f.name,
      url: adminChildFileDownloadUrl(filesBatchId.value, filesSessionId.value, f.path),
    }
    officePreviewVisible.value = true
    return
  }
  previewPath.value = f.path
  // 复用同 conv 风格：先清空 content 再开，避免上一文件预览残留
  previewContent.value = ''
  previewTruncated.value = false
  previewOpen.value = true
  previewLoading.value = true
  try {
    const res = await getAdminChildFilePreview(filesBatchId.value, filesSessionId.value, f.path)
    previewContent.value = res.content
    previewTruncated.value = res.truncated
  } finally {
    previewLoading.value = false
  }
}

async function onImportChildFiles(paths: string[]) {
  if (!paths.length) return
  try {
    const res = await importAdminChildFiles(filesBatchId.value, filesSessionId.value, paths)
    const results: AdminImportResult[] = res.results
    // 按 AdminImportResult 不变量:成功行带 status('imported'|'existing')、无 code;
    // 失败行带 code、无 status。两条互斥,所以 r.status / r.code 各自只命中一边。
    const ok = results.filter(r => r.status).length
    const fail = results.filter(r => r.code).length
    ElMessage.success(`已导入 ${ok} 个文件${fail ? `，${fail} 个失败` : ''}`)
    // 重新拉列表以刷新 dataFileId：不能乐观更新，导入是否生效要看服务端权威。
    const fresh = await listAdminChildFiles(filesBatchId.value, filesSessionId.value)
    childFiles.value = fresh.files
    childFilesTruncated.value = fresh.truncated
  } catch {
    // 全局 axios 拦截器已弹错；这里不再二次 toast。
  }
}

onMounted(reload)
onUnmounted(() => store.stopPolling())
</script>

<style scoped lang="scss">
.batch-admin__filters { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.batch-admin__poll-error { margin-bottom: 12px; }
.batch-admin__pager { margin-top: 12px; justify-content: flex-end; }
.batch-admin__actions { margin: 12px 0; }
.admin-preview__text {
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 60vh;
  overflow: auto;
  background: var(--el-fill-color-light);
  padding: 12px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  margin: 0 0 8px;
}
.admin-preview__hint { color: var(--el-text-color-secondary); padding: 12px 0; }
</style>
