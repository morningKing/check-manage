/**
 * Webhook 配置管理页面
 */
<template>
  <div class="webhook-settings-page">
    <el-card class="settings-card">
      <template #header>
        <div class="card-header">
          <span>合并通知 Webhook 配置</span>
          <el-tag v-if="settings.enabled" type="success" size="small">已启用</el-tag>
          <el-tag v-else type="info" size="small">未启用</el-tag>
        </div>
      </template>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="120px"
        v-loading="loading"
      >
        <el-form-item label="启用状态">
          <el-switch v-model="form.enabled" />
        </el-form-item>

        <el-form-item label="配置名称" prop="name">
          <el-input v-model="form.name" placeholder="如：合并通知" />
        </el-form-item>

        <el-form-item label="Webhook URL" prop="webhookUrl">
          <el-input v-model="form.webhookUrl" placeholder="https://example.com/webhook" />
          <div class="form-tip">
            合并成功后将向此 URL 发送 POST 请求，携带合并数据信息
          </div>
        </el-form-item>

        <el-form-item label="签名密钥" prop="secret">
          <el-input
            v-model="form.secret"
            placeholder="可选，用于签名验证"
            show-password
          />
          <div class="form-tip">
            使用 HMAC-SHA256 签名，请求头包含 X-Webhook-Signature 和 X-Webhook-Timestamp
          </div>
        </el-form-item>

        <el-form-item label="触发事件">
          <el-checkbox-group v-model="form.events">
            <el-checkbox value="merge">合并事件</el-checkbox>
          </el-checkbox-group>
        </el-form-item>

        <el-form-item label="超时时间">
          <el-input-number v-model="form.timeout" :min="5" :max="60" />
          <span class="unit">秒</span>
        </el-form-item>

        <el-form-item label="重试次数">
          <el-input-number v-model="form.retries" :min="0" :max="5" />
          <span class="unit">次</span>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="handleSave">
            保存配置
          </el-button>
          <el-button :loading="testing" @click="handleTest">
            测试 Webhook
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 测试结果 -->
    <el-card v-if="testResult" class="test-result-card">
      <template #header>
        <span>测试结果</span>
      </template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="状态">
          <el-tag :type="testResult.success ? 'success' : 'danger'">
            {{ testResult.success ? '成功' : '失败' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="响应状态码">
          {{ testResult.responseStatus || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="重试次数">
          {{ testResult.retryCount || 0 }}
        </el-descriptions-item>
        <el-descriptions-item label="日志ID">
          {{ testResult.logId || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="消息" :span="2">
          {{ testResult.message || testResult.errorMessage || '-' }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 调用日志 -->
    <el-card class="logs-card">
      <template #header>
        <div class="card-header">
          <span>调用日志</span>
          <div class="header-actions">
            <el-select v-model="logFilter.success" placeholder="状态筛选" clearable size="small" style="width: 100px">
              <el-option label="成功" value="true" />
              <el-option label="失败" value="false" />
            </el-select>
            <el-button size="small" @click="loadLogs">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table :data="logs" v-loading="logsLoading" stripe>
        <el-table-column prop="createdAt" label="时间" width="180">
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
          <template #default="{ row }">
            {{ row.durationMs }}ms
          </template>
        </el-table-column>
        <el-table-column prop="retryCount" label="重试" width="60" />
        <el-table-column prop="errorMessage" label="错误信息" min-width="200">
          <template #default="{ row }">
            <span v-if="row.errorMessage" class="error-text">{{ row.errorMessage }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="showLogDetail(row)">
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 日志详情对话框 -->
    <el-dialog v-model="showDetailDialog" title="Webhook 调用详情" width="600px">
      <el-descriptions v-if="currentLog" :column="1" border>
        <el-descriptions-item label="日志ID">{{ currentLog.id }}</el-descriptions-item>
        <el-descriptions-item label="时间">{{ formatTime(currentLog.createdAt) }}</el-descriptions-item>
        <el-descriptions-item label="事件">{{ currentLog.eventType }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="currentLog.success ? 'success' : 'danger'">
            {{ currentLog.success ? '成功' : '失败' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Webhook URL">{{ currentLog.webhookUrl }}</el-descriptions-item>
        <el-descriptions-item label="响应状态码">{{ currentLog.responseStatus || '-' }}</el-descriptions-item>
        <el-descriptions-item label="耗时">{{ currentLog.durationMs }}ms</el-descriptions-item>
        <el-descriptions-item label="重试次数">{{ currentLog.retryCount }}</el-descriptions-item>
      </el-descriptions>

      <div class="payload-section">
        <h4>请求 Payload</h4>
        <pre class="code-block">{{ JSON.stringify(currentLog?.requestPayload, null, 2) }}</pre>
      </div>

      <div v-if="currentLog?.responseBody" class="response-section">
        <h4>响应内容</h4>
        <pre class="code-block">{{ currentLog.responseBody }}</pre>
      </div>

      <div v-if="currentLog?.errorMessage" class="error-section">
        <h4>错误信息</h4>
        <pre class="code-block error">{{ currentLog.errorMessage }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import {
  getWebhookSettings,
  updateWebhookSettings,
  testWebhook,
  getWebhookLogs,
} from '@/api/webhook'
import type { WebhookSettings, WebhookLog, WebhookTestResult } from '@/types/webhook'

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const logsLoading = ref(false)
const formRef = ref<FormInstance>()
const testResult = ref<WebhookTestResult | null>(null)
const logs = ref<WebhookLog[]>([])
const showDetailDialog = ref(false)
const currentLog = ref<WebhookLog | null>(null)

const logFilter = reactive({
  success: '',
})

const settings = ref<WebhookSettings>({
  enabled: false,
  name: '',
  webhookUrl: '',
  secret: '',
  events: ['merge'],
  timeout: 30,
  retries: 3,
})

const form = reactive({
  enabled: false,
  name: '',
  webhookUrl: '',
  secret: '',
  events: ['merge'],
  timeout: 30,
  retries: 3,
})

const rules: FormRules = {
  webhookUrl: [
    { required: true, message: '请输入 Webhook URL', trigger: 'blur' },
    { type: 'url', message: '请输入有效的 URL', trigger: 'blur' },
  ],
}

function formatTime(time: string): string {
  if (!time) return '-'
  return new Date(time).toLocaleString()
}

async function loadSettings() {
  loading.value = true
  try {
    const data = await getWebhookSettings()
    settings.value = data
    Object.assign(form, data)
  } catch (e: any) {
    ElMessage.error(e?.message || '加载配置失败')
  } finally {
    loading.value = false
  }
}

async function loadLogs() {
  logsLoading.value = true
  try {
    const result = await getWebhookLogs({
      success: logFilter.success,
      limit: 20,
    })
    logs.value = result.logs
  } catch (e: any) {
    ElMessage.error(e?.message || '加载日志失败')
  } finally {
    logsLoading.value = false
  }
}

async function handleSave() {
  if (!formRef.value) return
  await formRef.value.validate()

  saving.value = true
  try {
    const data = await updateWebhookSettings({
      enabled: form.enabled,
      name: form.name,
      webhookUrl: form.webhookUrl,
      secret: form.secret,
      events: form.events,
      timeout: form.timeout,
      retries: form.retries,
    })
    settings.value = data
    ElMessage.success('配置已保存')
    loadLogs()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleTest() {
  testing.value = true
  testResult.value = null
  try {
    const result = await testWebhook({
      webhookUrl: form.webhookUrl || undefined,
    })
    testResult.value = result
    if (result.success) {
      ElMessage.success('Webhook 测试成功')
    } else {
      ElMessage.warning(`Webhook 测试失败: ${result.errorMessage || result.message}`)
    }
    loadLogs()
  } catch (e: any) {
    ElMessage.error(e?.message || '测试失败')
  } finally {
    testing.value = false
  }
}

function showLogDetail(log: WebhookLog) {
  currentLog.value = log
  showDetailDialog.value = true
}

onMounted(() => {
  loadSettings()
  loadLogs()
})
</script>

<style scoped lang="scss">
.webhook-settings-page {
  padding: 20px;
  max-width: 900px;
}

.settings-card,
.test-result-card,
.logs-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  gap: 10px;
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

.payload-section,
.response-section,
.error-section {
  margin-top: 16px;

  h4 {
    margin-bottom: 8px;
    font-size: 14px;
    color: #606266;
  }
}

.code-block {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
  font-size: 12px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;

  &.error {
    background: #fef0f0;
    color: #f56c6c;
  }
}
</style>