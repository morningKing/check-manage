<template>
  <div class="webhook-rule-manager">
    <div class="page-header">
      <h2>Webhook 管理</h2>
      <el-button type="primary" @click="handleAdd">新增规则</el-button>
    </div>

    <el-table :data="rules" v-loading="loading" border stripe>
      <el-table-column prop="name" label="名称" width="180" />
      <el-table-column prop="sourceCollections" label="数据页" width="200">
        <template #default="{ row }">
          <template v-if="row.sourceCollections && row.sourceCollections.length > 0">
            <el-tag v-for="col in row.sourceCollections.slice(0, 3)" :key="col" size="small" class="collection-tag">
              {{ getPageName(col) || col }}
            </el-tag>
            <span v-if="row.sourceCollections.length > 3" class="more-text">
              +{{ row.sourceCollections.length - 3 }}
            </span>
          </template>
          <el-tag v-else size="small" type="info">全局</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="triggerEvent" label="触发事件" width="120">
        <template #default="{ row }">
          <el-tag size="small">{{ eventLabels[row.triggerEvent] || row.triggerEvent }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="triggerTiming" label="触发时机" width="100">
        <template #default="{ row }">
          <el-tag :type="row.triggerTiming === 'before' ? 'warning' : 'success'" size="small">
            {{ row.triggerTiming === 'before' ? '操作前' : '操作后' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="webhookUrl" label="Webhook URL" min-width="250">
        <template #default="{ row }">
          <span class="url-text">{{ row.webhookUrl }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="enabled" label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
            {{ row.enabled ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button link @click="handleEdit(row)">编辑</el-button>
          <el-button link @click="handleTest(row)">测试</el-button>
          <el-button link @click="handleViewLogs(row)">日志</el-button>
          <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editVisible" :title="editForm.id ? '编辑规则' : '新增规则'" width="650px">
      <el-form ref="formRef" :model="editForm" :rules="formRules" label-width="110px">
        <el-form-item label="规则名称" prop="name">
          <el-input v-model="editForm.name" placeholder="如：订单创建通知" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="2" placeholder="规则说明" />
        </el-form-item>
        <el-form-item label="数据页">
          <el-select
            v-model="editForm.sourceCollections"
            multiple
            filterable
            clearable
            placeholder="选择数据页（可多选，留空表示全局）"
            style="width: 100%"
          >
            <el-option
              v-for="page in pageOptions"
              :key="page.collection"
              :label="page.name"
              :value="page.collection"
            />
          </el-select>
          <div class="form-tip">
            选择数据页后，规则仅对这些数据页生效；留空表示全局规则（如 merge 事件）
          </div>
        </el-form-item>
        <el-form-item label="触发事件" prop="triggerEvent">
          <el-select v-model="editForm.triggerEvent">
            <el-option label="创建" value="create" />
            <el-option label="更新" value="update" />
            <el-option label="删除" value="delete" />
            <el-option label="合并" value="merge" />
          </el-select>
        </el-form-item>
        <el-form-item label="触发时机">
          <el-radio-group v-model="editForm.triggerTiming">
            <el-radio value="before">操作前（可阻断）</el-radio>
            <el-radio value="after">操作后</el-radio>
          </el-radio-group>
          <div class="form-tip">
            操作前：webhook失败会阻止操作执行；操作后：webhook失败不影响已执行的操作
          </div>
        </el-form-item>
        <el-form-item label="失败回滚" v-if="editForm.triggerTiming === 'after' && editForm.triggerEvent === 'merge'">
          <el-switch v-model="editForm.rollbackOnFailure" />
          <div class="form-tip">启用后，after webhook失败时自动回滚merge操作（仅对merge事件有效）</div>
        </el-form-item>
        <el-form-item label="触发条件">
          <el-input v-model="conditionJson" type="textarea" :rows="2" placeholder='可选，如：{"field":"status","value":"completed"}' />
          <div class="form-tip">满足条件时才触发 webhook，留空表示无条件触发</div>
        </el-form-item>
        <el-form-item label="Webhook URL" prop="webhookUrl">
          <el-input v-model="editForm.webhookUrl" placeholder="https://example.com/webhook" />
        </el-form-item>
        <el-form-item label="签名密钥">
          <el-input v-model="editForm.secret" placeholder="可选，用于 HMAC-SHA256 签名" show-password />
          <div class="form-tip">使用 HMAC-SHA256 签名，请求头包含 X-Webhook-Signature</div>
        </el-form-item>
        <el-form-item label="超时时间">
          <el-input-number v-model="editForm.timeout" :min="5" :max="60" />
          <span class="unit">秒</span>
        </el-form-item>
        <el-form-item label="重试次数">
          <el-input-number v-model="editForm.retries" :min="0" :max="5" />
          <span class="unit">次</span>
        </el-form-item>
        <el-form-item label="执行顺序">
          <el-input-number v-model="editForm.executionOrder" :min="0" />
        </el-form-item>
        <el-form-item label="启用状态">
          <el-switch v-model="editForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 测试结果对话框 -->
    <el-dialog v-model="testVisible" title="Webhook 测试结果" width="500px">
      <el-descriptions :column="1" border v-if="testResult">
        <el-descriptions-item label="状态">
          <el-tag :type="testResult.success ? 'success' : 'danger'">
            {{ testResult.success ? '成功' : '失败' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="响应状态码">{{ testResult.responseStatus || '-' }}</el-descriptions-item>
        <el-descriptions-item label="重试次数">{{ testResult.retryCount || 0 }}</el-descriptions-item>
        <el-descriptions-item label="日志ID">{{ testResult.logId || '-' }}</el-descriptions-item>
        <el-descriptions-item label="错误信息">{{ testResult.errorMessage || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 日志对话框 -->
    <el-dialog v-model="logVisible" title="调用日志" width="800px">
      <el-table :data="logs" v-loading="logsLoading" border size="small">
        <el-table-column prop="createdAt" label="时间" width="170">
          <template #default="{ row }">
            {{ formatTime(row.createdAt) }}
          </template>
        </el-table-column>
        <el-table-column prop="eventType" label="事件" width="80">
          <template #default="{ row }">
            <el-tag size="small">{{ row.eventType }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="success" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.success ? 'success' : 'danger'" size="small">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="responseStatus" label="响应码" width="80" />
        <el-table-column prop="durationMs" label="耗时" width="80">
          <template #default="{ row }">{{ row.durationMs }}ms</template>
        </el-table-column>
        <el-table-column prop="retryCount" label="重试" width="60" />
        <el-table-column prop="errorMessage" label="错误信息" min-width="150">
          <template #default="{ row }">
            <span v-if="row.errorMessage" class="error-text">{{ row.errorMessage }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import {
  getWebhookRules,
  createWebhookRule,
  updateWebhookRule,
  deleteWebhookRule,
  testWebhookRule,
  getWebhookRuleLogs,
} from '@/api/webhook'
import { getPageConfigList } from '@/api/page'
import type { WebhookRule, WebhookRuleTestResult, WebhookLog } from '@/types/webhook'
import type { PageConfig } from '@/types'

const eventLabels: Record<string, string> = {
  create: '创建',
  update: '更新',
  delete: '删除',
  merge: '合并',
}

const loading = ref(false)
const saving = ref(false)
const rules = ref<WebhookRule[]>([])
const pageConfigs = ref<PageConfig[]>([])
const editVisible = ref(false)
const testVisible = ref(false)
const logVisible = ref(false)
const logsLoading = ref(false)
const formRef = ref<FormInstance>()
const testResult = ref<WebhookRuleTestResult | null>(null)
const logs = ref<WebhookLog[]>([])
const conditionJson = ref('{}')

// Compute page options for the select dropdown
const pageOptions = computed(() => {
  return pageConfigs.value.map(page => ({
    collection: page.id.replace('page-', ''),
    name: page.name,
  }))
})

// Create a map for quick lookup
const pageNameMap = computed(() => {
  const map: Record<string, string> = {}
  for (const page of pageConfigs.value) {
    map[page.id.replace('page-', '')] = page.name
  }
  return map
})

function getPageName(collection: string): string {
  return pageNameMap.value[collection] || collection
}

const editForm = reactive({
  id: '',
  name: '',
  description: '',
  sourceCollections: [] as string[],
  triggerEvent: 'create' as 'create' | 'update' | 'delete' | 'merge',
  triggerTiming: 'after' as 'before' | 'after',
  rollbackOnFailure: false,
  webhookUrl: '',
  secret: '',
  timeout: 30,
  retries: 3,
  executionOrder: 0,
  enabled: true,
})

const formRules: FormRules = {
  name: [{ required: true, message: '请输入规则名称', trigger: 'blur' }],
  triggerEvent: [{ required: true, message: '请选择触发事件', trigger: 'change' }],
  webhookUrl: [
    { required: true, message: '请输入 Webhook URL', trigger: 'blur' },
    { type: 'url', message: '请输入有效的 URL', trigger: 'blur' },
  ],
}

function formatTime(time: string): string {
  if (!time) return '-'
  return new Date(time).toLocaleString()
}

async function loadPageConfigs() {
  try {
    pageConfigs.value = await getPageConfigList()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载页面配置失败')
  }
}

async function loadRules() {
  loading.value = true
  try {
    rules.value = await getWebhookRules()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载规则失败')
  } finally {
    loading.value = false
  }
}

function handleAdd() {
  Object.assign(editForm, {
    id: '',
    name: '',
    description: '',
    sourceCollections: [],
    triggerEvent: 'create',
    triggerTiming: 'after',
    rollbackOnFailure: false,
    webhookUrl: '',
    secret: '',
    timeout: 30,
    retries: 3,
    executionOrder: 0,
    enabled: true,
  })
  conditionJson.value = '{}'
  editVisible.value = true
}

function handleEdit(row: WebhookRule) {
  Object.assign(editForm, {
    id: row.id,
    name: row.name,
    description: row.description || '',
    sourceCollections: row.sourceCollections || [],
    triggerEvent: row.triggerEvent,
    triggerTiming: row.triggerTiming || 'after',
    rollbackOnFailure: row.rollbackOnFailure || false,
    webhookUrl: row.webhookUrl,
    secret: row.secret || '',
    timeout: row.timeout,
    retries: row.retries,
    executionOrder: row.executionOrder,
    enabled: row.enabled,
  })
  conditionJson.value = JSON.stringify(row.triggerCondition || {}, null, 2)
  editVisible.value = true
}

async function handleSave() {
  if (!formRef.value) return
  await formRef.value.validate()

  let triggerCondition = {}
  try {
    triggerCondition = JSON.parse(conditionJson.value || '{}')
  } catch {
    ElMessage.error('触发条件 JSON 格式错误')
    return
  }

  saving.value = true
  try {
    const payload = {
      name: editForm.name,
      description: editForm.description,
      sourceCollections: editForm.sourceCollections,
      triggerEvent: editForm.triggerEvent,
      triggerTiming: editForm.triggerTiming,
      rollbackOnFailure: editForm.rollbackOnFailure,
      triggerCondition,
      webhookUrl: editForm.webhookUrl,
      secret: editForm.secret,
      timeout: editForm.timeout,
      retries: editForm.retries,
      executionOrder: editForm.executionOrder,
      enabled: editForm.enabled,
    }

    if (editForm.id) {
      await updateWebhookRule(editForm.id, payload)
    } else {
      await createWebhookRule(payload)
    }

    editVisible.value = false
    ElMessage.success('保存成功')
    await loadRules()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleTest(row: WebhookRule) {
  testResult.value = null
  testVisible.value = true
  try {
    testResult.value = await testWebhookRule(row.id)
    if (testResult.value.success) {
      ElMessage.success('Webhook 测试成功')
    } else {
      ElMessage.warning(`Webhook 测试失败: ${testResult.value.errorMessage}`)
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '测试失败')
  }
}

async function handleViewLogs(row: WebhookRule) {
  logsLoading.value = true
  logs.value = []
  logVisible.value = true
  try {
    const result = await getWebhookRuleLogs(row.id, { limit: 20 })
    logs.value = result.logs
  } catch (e: any) {
    ElMessage.error(e?.message || '加载日志失败')
  } finally {
    logsLoading.value = false
  }
}

async function handleDelete(row: WebhookRule) {
  try {
    await ElMessageBox.confirm(`确定删除规则「${row.name}」？`, '确认删除')
    await deleteWebhookRule(row.id)
    ElMessage.success('已删除')
    await loadRules()
  } catch {
    // cancelled
  }
}

onMounted(() => {
  loadPageConfigs()
  loadRules()
})
</script>

<style scoped>
.webhook-rule-manager {
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

.url-text {
  color: #606266;
  font-size: 13px;
}

.collection-tag {
  margin-right: 4px;
}

.more-text {
  color: #909399;
  font-size: 12px;
}

.form-tip {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}

.unit {
  margin-left: 8px;
  color: #606266;
}

.error-text {
  color: #f56c6c;
}
</style>