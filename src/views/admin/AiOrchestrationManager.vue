<template>
  <div class="orch-manager">
    <el-tabs v-model="activeTab">
      <!-- ── Tab 1: 定义 ─────────────────────────────────────────── -->
      <el-tab-pane label="定义" name="definitions">
        <div class="orch-manager__toolbar">
          <el-button type="primary" @click="reloadDefinitions">刷新</el-button>
          <el-button v-if="canPublish" type="primary" plain @click="openPublishDialog">
            发布定义
          </el-button>
        </div>
        <el-table :data="definitions" v-loading="definitionsLoading" style="width: 100%">
          <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip />
          <el-table-column prop="id" label="ID" width="170" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="orch-manager__mono">{{ row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column label="版本" width="80">
            <template #default="{ row }">v{{ row.version }}</template>
          </el-table-column>
          <el-table-column label="描述" min-width="240" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.description">{{ row.description }}</span>
              <span v-else class="el-text-color-secondary">—</span>
            </template>
          </el-table-column>
          <el-table-column label="发布时间" width="170">
            <template #default="{ row }">{{ fmt(row.publishedAt) }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ── Tab 2: 运行 ─────────────────────────────────────────── -->
      <el-tab-pane label="运行" name="runs">
        <div class="orch-manager__toolbar">
          <el-button type="primary" @click="reloadRuns">刷新</el-button>
        </div>
        <el-table :data="runs" v-loading="runsLoading" style="width: 100%" row-key="id"
                  @expand-change="onRunExpand">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div v-loading="stepLoading[row.id]" class="orch-manager__steps">
                <template v-if="runSteps[row.id]">
                  <el-table :data="runSteps[row.id]" size="small" border>
                    <el-table-column prop="node_id" label="节点" min-width="140" show-overflow-tooltip />
                    <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
                    <el-table-column prop="kind" label="类型" width="90" />
                    <el-table-column label="状态" width="110">
                      <template #default="{ row: step }">
                        <el-tag size="small" :type="stepTagType(step.status)">{{ step.status }}</el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column label="尝试" width="70">
                      <template #default="{ row: step }">{{ step.attempt_count }}</template>
                    </el-table-column>
                    <el-table-column label="开始" width="170">
                      <template #default="{ row: step }">{{ fmt(step.started_at) }}</template>
                    </el-table-column>
                    <el-table-column label="结束" width="170">
                      <template #default="{ row: step }">{{ fmt(step.finished_at) }}</template>
                    </el-table-column>
                    <el-table-column label="错误" min-width="200" show-overflow-tooltip>
                      <template #default="{ row: step }">
                        <span v-if="step.error_message" class="orch-manager__error">{{ step.error_message }}</span>
                        <span v-else class="el-text-color-secondary">—</span>
                      </template>
                    </el-table-column>
                  </el-table>
                </template>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="运行 ID" width="190" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="orch-manager__mono">{{ row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="130">
            <template #default="{ row }">
              <el-tag size="small" :type="runTagType(row.status)">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="定义" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="orch-manager__mono">{{ row.definition_id }}</span>
              <el-tag size="small" type="info" style="margin-left:4px">v{{ row.definition_version }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="requested_by" label="发起人" width="130" show-overflow-tooltip />
          <el-table-column label="创建时间" width="170">
            <template #default="{ row }">{{ fmt(row.createdAt) }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- ── 发布定义对话框（JSON 编辑） ────────────────────────────── -->
    <el-dialog v-model="publishVisible" title="发布编排定义（JSON）" width="640px" :close-on-click-modal="false">
      <el-alert type="info" show-icon :closable="false" class="orch-manager__publish-tip"
                title="定义发布后不可变：同 id 再次发布即新版本。JSON 须含 name 与非空 nodes 数组。" />
      <el-input v-model="publishJson" type="textarea" :rows="16" class="orch-manager__publish-json"
                placeholder='{"name": "...", "description": "...", "nodes": [...], "edges": [...]}' />
      <template #footer>
        <el-button @click="publishVisible = false">取消</el-button>
        <el-button type="primary" :loading="publishing" @click="onPublish">发布</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import {
  listDefinitions, publishDefinition, listRuns, getRun,
  type OrchDefinition, type OrchPublishBody, type OrchRun, type OrchStep,
} from '@/api/orchestration'

const auth = useAuthStore()
const canPublish = computed(() => auth.can('admin.ai_orchestration_admin'))

// ── 定义 tab ────────────────────────────────────────────────────────
const activeTab = ref('definitions')
const definitions = ref<OrchDefinition[]>([])
const definitionsLoading = ref(false)

async function reloadDefinitions() {
  definitionsLoading.value = true
  try {
    const res = await listDefinitions()
    definitions.value = res.definitions || []
  } finally {
    definitionsLoading.value = false
  }
}

// ── 运行 tab（展开行懒加载 steps） ──────────────────────────────────
const runs = ref<OrchRun[]>([])
const runsLoading = ref(false)
const runSteps = reactive<Record<string, OrchStep[]>>({})
const stepLoading = reactive<Record<string, boolean>>({})

async function reloadRuns() {
  runsLoading.value = true
  try {
    const res = await listRuns()
    runs.value = res.runs || []
  } finally {
    runsLoading.value = false
  }
}

async function onRunExpand(row: OrchRun, expandedRows: OrchRun[]) {
  if (!expandedRows.includes(row) || runSteps[row.id]) return
  stepLoading[row.id] = true
  try {
    const detail = await getRun(row.id)
    runSteps[row.id] = detail.steps || []
  } finally {
    stepLoading[row.id] = false
  }
}

// ── 发布对话框 ──────────────────────────────────────────────────────
const publishVisible = ref(false)
const publishing = ref(false)
const publishJson = ref('')

function openPublishDialog() {
  if (!publishJson.value) {
    publishJson.value = JSON.stringify({
      name: 'example-orchestration',
      description: '两步串行编排示例',
      nodes: [
        { id: 'step-1', kind: 'agent', prompt_template: '做第一件事' },
        { id: 'step-2', kind: 'agent', prompt_template: '做第二件事' },
      ],
      edges: [{ source: 'step-1', target: 'step-2', kind: 'advance' }],
    }, null, 2)
  }
  publishVisible.value = true
}

async function onPublish() {
  let body: unknown
  try {
    body = JSON.parse(publishJson.value)
  } catch {
    ElMessage.warning('JSON 解析失败，请检查格式')
    return
  }
  const b = (body || {}) as Record<string, unknown>
  if (!b.name || typeof b.name !== 'string' || !b.name.trim()) {
    ElMessage.warning('缺少 name 字段')
    return
  }
  if (!Array.isArray(b.nodes) || b.nodes.length === 0) {
    ElMessage.warning('nodes 必须是非空数组')
    return
  }
  publishing.value = true
  try {
    const res = await publishDefinition(b as unknown as OrchPublishBody)
    ElMessage.success(`已发布 ${res.id} v${res.version}`)
    publishVisible.value = false
    activeTab.value = 'definitions'
    await reloadDefinitions()
  } finally {
    publishing.value = false
  }
}

// ── 工具 ────────────────────────────────────────────────────────────
function fmt(v: string | null): string {
  if (!v) return '—'
  return String(v).replace('T', ' ').slice(0, 19)
}

// 状态枚举与 utils/orchestration_engine.py 对齐：
// RUN_ACTIVE / STEP_TERMINAL + run 终态 completed/partial/needs_review/cancelled/failed
const RUN_ACTIVE_STATUSES = new Set(['pending', 'running', 'waiting_approval', 'recovering'])

function runTagType(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  if (status === 'completed') return 'success'
  if (RUN_ACTIVE_STATUSES.has(status)) return 'primary'
  if (status === 'failed' || status === 'cancelled') return 'danger'
  if (status === 'partial' || status === 'needs_review' || status === 'suspended') return 'warning'
  return 'info'
}

function stepTagType(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  if (status === 'succeeded') return 'success'
  if (status === 'running' || status === 'pending') return 'primary'
  if (status === 'failed' || status === 'timeout') return 'danger'
  if (status === 'blocked' || status === 'suspended' || status === 'needs_review') return 'warning'
  return 'info'
}

onMounted(() => {
  reloadDefinitions()
  reloadRuns()
})
</script>

<style scoped>
.orch-manager {
  padding: 0 4px;
}

.orch-manager__toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.orch-manager__mono {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
}

.orch-manager__steps {
  padding: 8px 16px 12px 48px;
}

.orch-manager__error {
  color: var(--el-color-danger);
}

.orch-manager__publish-tip {
  margin-bottom: 12px;
}

.orch-manager__publish-json :deep(textarea) {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
}
</style>
