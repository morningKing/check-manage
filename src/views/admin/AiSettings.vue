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

        <el-form-item label="长期记忆 (mem0)">
          <el-switch v-model="settings.mem0Enabled" />
          <span class="hint">开启后，AI 会话将自动形成并调用按用户的长期记忆</span>
        </el-form-item>

        <el-form-item label="Embedding 模型" v-if="settings.mem0Enabled">
          <el-input
            v-model="settings.embeddingModel"
            placeholder="text-embedding-v3"
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

    <el-card class="mcp-card">
      <template #header>
        <div class="card-header">
          <h2>外部 MCP 服务</h2>
          <el-button type="primary" size="small" style="margin-left: auto" @click="openMcpDialog()">
            添加 MCP
          </el-button>
        </div>
      </template>

      <p class="hint hint--block">
        在此登记的外部 MCP 服务会自动合并进每个 AI 会话的 <code>opencode.json</code>，供助手调用其工具（与平台自带 MCP 并存）。修改后对**新建 / 清空（重置）**的会话生效。
      </p>

      <el-table :data="mcpServers" v-loading="mcpLoading" size="small">
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column prop="type" label="类型" width="90">
          <template #default="{ row }">{{ row.type === 'remote' ? '远程' : '本地' }}</template>
        </el-table-column>
        <el-table-column label="地址 / 命令" min-width="220">
          <template #default="{ row }">
            <span class="mono">{{ row.type === 'remote' ? row.url : row.command.join(' ') }}</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v: string | number | boolean) => toggleMcp(row, !!v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button size="small" text @click="openMcpDialog(row)">编辑</el-button>
            <el-button size="small" text type="danger" @click="removeMcp(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!mcpLoading && !mcpServers.length" description="暂无外部 MCP 服务" :image-size="60" />
    </el-card>

    <el-dialog v-model="mcpDialog.open" :title="mcpDialog.id ? '编辑 MCP 服务' : '添加 MCP 服务'" width="560px">
      <el-form label-width="92px">
        <el-form-item label="名称" required>
          <el-input v-model="mcpForm.name" placeholder="唯一名称，用作 opencode.json 的 key" />
        </el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="mcpForm.type">
            <el-radio-button value="remote">远程 (URL)</el-radio-button>
            <el-radio-button value="local">本地 (命令)</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="mcpForm.type === 'remote'" label="URL" required>
          <el-input v-model="mcpForm.url" placeholder="https://host/mcp" />
        </el-form-item>
        <el-form-item v-if="mcpForm.type === 'remote'" label="请求头">
          <el-input v-model="mcpForm.headersText" type="textarea" :rows="3"
            placeholder="每行一个 KEY=值（可选），如 Authorization=Bearer xxx" />
        </el-form-item>
        <el-form-item v-if="mcpForm.type === 'local'" label="命令" required>
          <el-input v-model="mcpForm.commandText" type="textarea" :rows="3"
            placeholder="每行一个参数，例如：&#10;npx&#10;-y&#10;some-mcp-server" />
        </el-form-item>
        <el-form-item v-if="mcpForm.type === 'local'" label="环境变量">
          <el-input v-model="mcpForm.envText" type="textarea" :rows="3"
            placeholder="每行一个 KEY=值（可选）" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="mcpForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mcpDialog.open = false">取消</el-button>
        <el-button type="primary" :loading="mcpSaving" @click="saveMcp">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get } from '@/utils/request'
import service from '@/utils/request'
import {
  listMcpServers, createMcpServer, updateMcpServer, deleteMcpServer,
  type McpServer,
} from '@/api/aiMcpServers'

interface AiSettingsData {
  enabled: boolean
  apiKey: string
  endpoint: string
  model: string
  timeout: number
  maxTokens: number
  mem0Enabled?: boolean
  embeddingModel?: string
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
  mem0Enabled: false,
  embeddingModel: 'text-embedding-v3',
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
    settings.mem0Enabled = !!s.mem0Enabled
    settings.embeddingModel = s.embeddingModel || 'text-embedding-v3'
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
      mem0Enabled: settings.mem0Enabled,
      embeddingModel: settings.embeddingModel,
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

// --- External MCP servers ---------------------------------------------------
const mcpServers = ref<McpServer[]>([])
const mcpLoading = ref(false)
const mcpSaving = ref(false)
const mcpDialog = reactive<{ open: boolean; id: string | null }>({ open: false, id: null })
const mcpForm = reactive({
  name: '', type: 'remote' as 'remote' | 'local', url: '',
  headersText: '', commandText: '', envText: '', enabled: true,
})

function parseKv(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const t = line.trim()
    if (!t) continue
    const i = t.indexOf('=')
    if (i < 0) continue
    out[t.slice(0, i).trim()] = t.slice(i + 1).trim()
  }
  return out
}
function kvToText(o: Record<string, string> | undefined): string {
  return Object.entries(o || {}).map(([k, v]) => `${k}=${v}`).join('\n')
}
function parseLines(text: string): string[] {
  return text.split('\n').map((s) => s.trim()).filter(Boolean)
}

async function loadMcp() {
  mcpLoading.value = true
  try { mcpServers.value = await listMcpServers() } catch { /* surfaced */ } finally { mcpLoading.value = false }
}

function openMcpDialog(row?: McpServer) {
  if (row) {
    mcpDialog.id = row.id
    mcpForm.name = row.name
    mcpForm.type = row.type
    mcpForm.url = row.url
    mcpForm.headersText = kvToText(row.headers)
    mcpForm.commandText = (row.command || []).join('\n')
    mcpForm.envText = kvToText(row.environment)
    mcpForm.enabled = row.enabled
  } else {
    mcpDialog.id = null
    mcpForm.name = ''
    mcpForm.type = 'remote'
    mcpForm.url = ''
    mcpForm.headersText = ''
    mcpForm.commandText = ''
    mcpForm.envText = ''
    mcpForm.enabled = true
  }
  mcpDialog.open = true
}

function mcpPayload() {
  return {
    name: mcpForm.name.trim(),
    type: mcpForm.type,
    url: mcpForm.type === 'remote' ? mcpForm.url.trim() : '',
    command: mcpForm.type === 'local' ? parseLines(mcpForm.commandText) : [],
    headers: mcpForm.type === 'remote' ? parseKv(mcpForm.headersText) : {},
    environment: mcpForm.type === 'local' ? parseKv(mcpForm.envText) : {},
    enabled: mcpForm.enabled,
  }
}

async function saveMcp() {
  const body = mcpPayload()
  if (!body.name) { ElMessage.warning('请填写名称'); return }
  if (body.type === 'remote' && !body.url) { ElMessage.warning('请填写 URL'); return }
  if (body.type === 'local' && !body.command.length) { ElMessage.warning('请填写命令'); return }
  mcpSaving.value = true
  try {
    if (mcpDialog.id) await updateMcpServer(mcpDialog.id, body)
    else await createMcpServer(body)
    ElMessage.success('已保存')
    mcpDialog.open = false
    await loadMcp()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '保存失败')
  } finally {
    mcpSaving.value = false
  }
}

async function toggleMcp(row: McpServer, enabled: boolean) {
  try {
    await updateMcpServer(row.id, { ...row, enabled })
    row.enabled = enabled
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '更新失败')
  }
}

async function removeMcp(row: McpServer) {
  try {
    await ElMessageBox.confirm(`确认删除 MCP 服务「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteMcpServer(row.id)
    ElMessage.success('已删除')
    await loadMcp()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '删除失败')
  }
}

onMounted(() => {
  loadSettings()
  loadMcp()
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

.hint {
  margin-left: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.updated-info {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.mcp-card {
  margin-top: 16px;
}

.hint--block {
  margin: 0 0 12px;
  display: block;
}

.mono {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  word-break: break-all;
}
</style>
