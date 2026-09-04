<template>
  <div class="session-admin">
    <!-- Filter bar -->
    <div class="session-admin__filters">
      <el-select v-model="sourceType" placeholder="来源类型" clearable style="width: 140px"
                 @change="onFilterChange('sourceType', $event)">
        <el-option v-for="s in SOURCE_TYPES" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-select v-model="status" placeholder="状态" clearable style="width: 130px"
                 @change="onFilterChange('status', $event)">
        <el-option v-for="s in STATUSES" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-input v-model="owner" placeholder="用户名" clearable style="width: 160px"
                @change="onFilterChange('owner', $event)" />
      <el-input v-model="keyword" placeholder="标题/文件名/消息" clearable style="width: 200px"
                @change="onFilterChange('keyword', $event)" />
      <el-input v-model="batchId" placeholder="批任务 ID" clearable style="width: 200px"
                @change="onFilterChange('batchId', $event)" />
      <el-button type="primary" @click="reload">查询</el-button>
      <el-button @click="clearAndReload">重置</el-button>
    </div>

    <el-alert v-if="store.pollError" type="warning" show-icon :closable="false"
              class="session-admin__poll-error"
              title="后台自动刷新已连续多次失败并已停止">
      <el-button size="small" type="warning" plain @click="reload">重新加载</el-button>
    </el-alert>

    <!-- Session table -->
    <el-table :data="store.items" v-loading="store.loading" style="width: 100%"
              @row-click="onRowClick" highlight-current-row>
      <el-table-column prop="id" label="会话 ID" width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="session-admin__sid" :title="row.id">{{ row.id }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="username" label="用户" width="120" />
      <el-table-column label="来源" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="sourceTagType(row.sourceType)">
            {{ sourceLabel(row.sourceType) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="标题/输入文件" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.title || inputFileDisplay(row.inputFile) || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="批任务" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <template v-if="row.batchId">
            <span>{{ row.batchName || row.batchId }}</span>
            <el-tag v-if="row.batchSeq != null" size="small" type="info" style="margin-left:4px">
              #{{ row.batchSeq }}
            </el-tag>
          </template>
          <span v-else class="el-text-color-secondary">—</span>
        </template>
      </el-table-column>
      <el-table-column label="最后消息" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="el-text-color-secondary">{{ row.lastMessagePreview || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="最后活跃" width="170">
        <template #default="{ row }">{{ fmt(row.lastActiveAt || row.createdAt) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="80" fixed="right">
        <template #default="{ row }">
          <el-dropdown trigger="click" @command="(cmd: string) => onRowAction(cmd, row)">
            <el-button link type="primary">
              操作 <el-icon><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="detail">详情</el-dropdown-item>
                <el-dropdown-item command="analyze">
                  <span style="color: #67c23a">轨迹分析</span>
                </el-dropdown-item>
                <el-dropdown-item v-if="row.status === 'active'" command="archive">
                  <span style="color: #e6a23c">归档</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination class="session-admin__pager" background layout="total, prev, pager, next"
                   :total="store.total" :current-page="store.page" :page-size="store.pageSize"
                   @current-change="onPage" />

    <!-- Detail drawer -->
    <el-drawer v-model="store.detailVisible" :title="drawerTitle" size="65%" destroy-on-close>
      <el-tabs v-model="store.activeTab" @tab-change="onTabChange">
        <!-- Tab 1: Basic info -->
        <el-tab-pane label="基本信息" name="info">
          <div v-loading="store.detailLoading">
            <el-descriptions v-if="store.detail" :column="2" border size="small">
              <el-descriptions-item label="会话 ID">
                <span class="session-admin__sid">{{ store.detail.id }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="用户">{{ store.detail.username }}</el-descriptions-item>
              <el-descriptions-item label="来源">
                <el-tag size="small" :type="sourceTagType(store.detail.sourceType)">
                  {{ sourceLabel(store.detail.sourceType) }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="状态">
                <el-tag size="small" :type="statusTagType(store.detail.status)">
                  {{ statusLabel(store.detail.status) }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="标题" :span="2">
                {{ store.detail.title || '—' }}
              </el-descriptions-item>
              <el-descriptions-item v-if="store.detail.batchId" label="所属批任务">
                {{ store.detail.batchName || store.detail.batchId }}
                <el-tag v-if="store.detail.batchSeq != null" size="small" type="info" style="margin-left:4px">
                  #{{ store.detail.batchSeq }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item v-if="store.detail.batchId" label="输入文件">
                {{ inputFileDisplay(store.detail.inputFile) || '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="OpenCode 会话 ID">
                <span class="session-admin__sid">{{ store.detail.opencodeSessionId || '—' }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="工作区路径">
                <span class="session-admin__path">{{ store.detail.workspacePath || '—' }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="创建时间">{{ fmt(store.detail.createdAt) }}</el-descriptions-item>
              <el-descriptions-item label="最后活跃">{{ fmt(store.detail.lastActiveAt) }}</el-descriptions-item>
              <el-descriptions-item v-if="store.detail.errorMessage" label="错误信息" :span="2">
                <el-text type="danger">{{ store.detail.errorMessage }}</el-text>
              </el-descriptions-item>
            </el-descriptions>
            <div style="margin-top: 16px">
              <el-button type="primary" :loading="analyzing" @click="onAnalyze(store.detail!.id)">
                轨迹分析
              </el-button>
            </div>
          </div>
        </el-tab-pane>

        <!-- Tab 2: Messages (conversation) -->
        <el-tab-pane label="对话历史" name="messages">
          <div v-if="store.messagesLoading" v-loading="true" style="height: 200px" />
          <BatchConversationView
            v-else
            :messages="store.messages"
            :truncated="store.messagesTruncated"
            :total="store.messagesTotal"
            :loading="store.messagesLoading"
            :session-id="store.detail?.id || ''"
            :fetch-subtask-fn="fetchSubtaskFn"
          />
        </el-tab-pane>

        <!-- Tab 3: Files -->
        <el-tab-pane label="文件" name="files">
          <div v-if="store.filesLoading" v-loading="true" style="height: 200px" />
          <div v-else-if="store.files.length === 0" class="el-text-color-secondary" style="padding: 20px">
            该会话没有工作区或工作区为空
          </div>
          <div v-else class="session-admin__files">
            <div v-for="group in fileGroups" :key="group.dir" class="session-admin__file-group">
              <div class="session-admin__file-group-title">{{ group.label }}</div>
              <div v-for="f in group.files" :key="f.path" class="session-admin__file-item">
                <span class="session-admin__file-name">{{ f.name }}</span>
                <span class="session-admin__file-path el-text-color-secondary">{{ f.path }}</span>
                <span class="session-admin__file-size el-text-color-secondary">{{ formatSize(f.size) }}</span>
                <el-button link type="primary" size="small" @click="downloadFile(f.path)">下载</el-button>
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
import { useAiSessionAdminStore } from '@/stores/aiSessionAdmin'
import BatchConversationView from '@/components/ai-chat/BatchConversationView.vue'
import { getSessionMessages, sessionFileDownloadUrl, analyzeSession } from '@/api/aiSessionAdmin'

const store = useAiSessionAdminStore()
const analyzing = ref(false)

// Filter local state
const sourceType = ref('')
const status = ref('')
const owner = ref('')
const keyword = ref('')
const batchId = ref('')

const SOURCE_TYPES = [
  { label: '交互式', value: 'regular' },
  { label: '批任务(界面)', value: 'batch' },
  { label: '批任务(API)', value: 'api_batch' },
  { label: '定时扫描', value: 'scan' },
  { label: '客服', value: 'kefu' },
]

const STATUSES = [
  { label: '活跃', value: 'active' },
  { label: '已关闭', value: 'closed' },
  { label: '已删除', value: 'deleted' },
  { label: '已归档', value: 'archived' },
  { label: '待处理', value: 'pending' },
  { label: '运行中', value: 'running' },
  { label: '已完成', value: 'completed' },
  { label: '已失败', value: 'failed' },
]

const drawerTitle = computed(() => {
  const d = store.detail
  if (!d) return '会话详情'
  const label = d.title || inputFileDisplay(d.inputFile) || d.id
  return label.length > 40 ? label.slice(0, 40) + '...' : label
})

const fileGroups = computed(() => {
  const DIR_LABELS: Record<string, string> = {
    outputs: 'outputs/（agent 输出）',
    workspace: '根目录 / 子目录',
    uploads: 'uploads/（用户输入）',
  }
  const m = new Map<string, typeof store.files>()
  for (const f of store.files) {
    if (!m.has(f.dir)) m.set(f.dir, [])
    m.get(f.dir)!.push(f)
  }
  return Array.from(m.entries())
    .sort((a) => (a[0] === 'outputs' ? -1 : 1))
    .map(([dir, files]) => ({ dir, files, label: DIR_LABELS[dir] || dir }))
})

function onFilterChange(key: string, value: string) {
  store.setFilter(key as any, value || '')
}

function reload() {
  store.fetchList()
  if (store.hasRunning) store.startPolling()
  else store.stopPolling()
}

function clearAndReload() {
  sourceType.value = ''
  status.value = ''
  owner.value = ''
  keyword.value = ''
  batchId.value = ''
  store.clearFilters()
  reload()
}

function onPage(p: number) {
  store.page = p
  reload()
}

function onRowClick(row: any) {
  openDetail(row.id)
}

async function openDetail(sessionId: string) {
  await store.openDetail(sessionId)
  // Auto-load messages and files
  store.fetchMessages()
  store.fetchFiles()
}

function onTabChange(tab: string) {
  if (tab === 'messages' && store.messages.length === 0) store.fetchMessages()
  if (tab === 'files' && store.files.length === 0) store.fetchFiles()
}

async function onArchive(sessionId: string) {
  try {
    await ElMessageBox.confirm('确定归档此会话？', '确认')
    await store.doArchive(sessionId)
    ElMessage.success('已归档')
  } catch { /* cancelled */ }
}

async function onAnalyze(sessionId: string) {
  analyzing.value = true
  try {
    const res = await analyzeSession(sessionId)
    ElMessage.success(res.message || '轨迹分析已触发')
    // Open the analysis session in a new tab
    window.open(`/ai-chat?session=${res.analysisSessionId}`, '_blank')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || e?.message || '触发分析失败')
  } finally {
    analyzing.value = false
  }
}

function onRowAction(command: string, row: any) {
  if (command === 'detail') openDetail(row.id)
  else if (command === 'analyze') onAnalyze(row.id)
  else if (command === 'archive') onArchive(row.id)
}

function downloadFile(path: string) {
  if (!store.detail) return
  window.open(sessionFileDownloadUrl(store.detail.id, path))
}

// Subtask fetch function for BatchConversationView
async function fetchSubtaskFn(_sessionId: string, subtaskId: string) {
  const res = await getSessionMessages(subtaskId)
  return {
    messages: res.messages as any[],
    truncated: res.truncated,
    total: res.total,
    subtask: null as any,
  }
}

function inputFileDisplay(path: string | null | undefined): string {
  if (!path) return ''
  return path.replace(/\\/g, '/').split('/').pop() || path
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  return d.toLocaleString('zh-CN', { hour12: false })
}

function sourceTagType(s: string) {
  if (s === 'api_batch') return 'warning'
  if (s === 'batch') return 'info'
  if (s === 'scan') return 'success'
  if (s === 'kefu') return 'danger'
  return ''
}

function sourceLabel(s: string) {
  const map: Record<string, string> = {
    regular: '交互式', batch: '批任务', api_batch: 'API批任务', scan: '扫描', kefu: '客服',
  }
  return map[s] || s
}

function statusTagType(s: string) {
  if (['completed'].includes(s)) return 'success'
  if (['failed', 'deleted'].includes(s)) return 'danger'
  if (['running', 'pending'].includes(s)) return 'warning'
  if (['archived', 'closed'].includes(s)) return 'info'
  return ''
}

function statusLabel(s: string) {
  const map: Record<string, string> = {
    active: '活跃', closed: '已关闭', deleted: '已删除', archived: '已归档',
    pending: '待处理', running: '运行中', completed: '已完成', failed: '已失败',
  }
  return map[s] || s
}

onMounted(() => {
  reload()
})

onBeforeUnmount(() => {
  store.stopPolling()
})
</script>

<style scoped lang="scss">
.session-admin {
  padding: 20px;

  &__filters {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }

  &__poll-error {
    margin-bottom: 12px;
  }

  &__pager {
    margin-top: 16px;
    display: flex;
    justify-content: flex-end;
  }

  &__sid {
    font-family: monospace;
    font-size: 12px;
    color: var(--el-text-color-secondary);
    cursor: pointer;
  }

  &__path {
    font-family: monospace;
    font-size: 12px;
    word-break: break-all;
  }

  &__files {
    padding: 8px 0;
  }

  &__file-group {
    margin-bottom: 16px;
  }

  &__file-group-title {
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 8px;
    color: var(--el-text-color-primary);
  }

  &__file-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 4px 0;
    font-size: 13px;
  }

  &__file-name {
    min-width: 120px;
    font-weight: 500;
  }

  &__file-path {
    flex: 1;
    font-family: monospace;
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__file-size {
    min-width: 70px;
    text-align: right;
  }
}
</style>
