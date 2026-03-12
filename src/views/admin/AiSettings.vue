/**
 * AI 智能查询配置页面
 *
 * 管理员配置 AI 查询所需的 API Key、端点、模型等参数。
 * 沿用 backup_settings 单例表模式。
 */
<template>
  <div class="ai-settings">
    <el-card>
      <template #header>
        <div class="card-header">
          <h2>AI 智能查询配置</h2>
        </div>
      </template>

      <el-form
        ref="formRef"
        :model="settings"
        label-width="120px"
        style="max-width: 600px"
        v-loading="loading"
      >
        <el-form-item label="启用 AI 查询">
          <el-switch v-model="settings.enabled" />
        </el-form-item>

        <el-form-item label="API Key" required>
          <el-input
            v-model="settings.apiKey"
            placeholder="请输入 API Key"
            show-password
            :disabled="!settings.enabled"
          />
        </el-form-item>

        <el-form-item label="API 端点" required>
          <el-input
            v-model="settings.endpoint"
            placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
            :disabled="!settings.enabled"
          />
        </el-form-item>

        <el-form-item label="模型" required>
          <el-input
            v-model="settings.model"
            placeholder="qwen-plus"
            :disabled="!settings.enabled"
          />
        </el-form-item>

        <el-form-item label="超时 (秒)">
          <el-input-number
            v-model="settings.timeout"
            :min="5"
            :max="120"
            :disabled="!settings.enabled"
            style="width: 180px"
          />
        </el-form-item>

        <el-form-item label="Max Tokens">
          <el-input-number
            v-model="settings.maxTokens"
            :min="64"
            :max="8192"
            :step="256"
            :disabled="!settings.enabled"
            style="width: 180px"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="handleSave" :loading="saving">
            保存设置
          </el-button>
        </el-form-item>
      </el-form>

      <div v-if="settings.updatedAt" class="updated-info">
        上次更新：{{ formatDate(settings.updatedAt) }}
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { get } from '@/utils/request'
import service from '@/utils/request'

interface AiSettingsData {
  enabled: boolean
  apiKey: string
  endpoint: string
  model: string
  timeout: number
  maxTokens: number
  updatedAt: string | null
}

const loading = ref(false)
const saving = ref(false)

const settings = reactive<AiSettingsData>({
  enabled: false,
  apiKey: '',
  endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
  model: 'qwen-plus',
  timeout: 30,
  maxTokens: 1024,
  updatedAt: null,
})

function formatDate(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN')
}

async function loadSettings() {
  loading.value = true
  try {
    const s = await get<AiSettingsData>('/ai/settings')
    settings.enabled = s.enabled
    settings.apiKey = s.apiKey
    settings.endpoint = s.endpoint
    settings.model = s.model
    settings.timeout = s.timeout
    settings.maxTokens = s.maxTokens
    settings.updatedAt = s.updatedAt
  } catch {
    // silent
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  saving.value = true
  try {
    const { data } = await service.put('/ai/settings', {
      enabled: settings.enabled,
      apiKey: settings.apiKey,
      endpoint: settings.endpoint,
      model: settings.model,
      timeout: settings.timeout,
      maxTokens: settings.maxTokens,
    }) as any
    // Update local state with response
    if (data) {
      settings.apiKey = data.apiKey
      settings.updatedAt = data.updatedAt
    }
    ElMessage.success('设置已保存')
  } catch {
    ElMessage.error('保存设置失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadSettings()
})
</script>

<style scoped>
.ai-settings {
  padding: 0;
}

.card-header {
  display: flex;
  align-items: center;
}

.card-header h2 {
  margin: 0;
  font-size: 16px;
}

.updated-info {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
