<!-- AI 会话的 MCP 服务管理卡片（原 AI 配置页迁入，与 AI 技能管理合并展示）。
     包含平台内置 MCP（开关 + 连接健康）与外部 MCP 服务的增删改查，
     全部合并进每个 AI 会话的 opencode.json，只对新建/清空的会话生效。 -->
<template>
  <el-card class="mcp-card">
    <template #header>
      <div class="mcp-card__header">
        <h2>MCP 服务</h2>
        <el-button type="primary" size="small" style="margin-left: auto" @click="openMcpDialog()">
          添加外部 MCP
        </el-button>
      </div>
    </template>

    <p class="hint hint--block">
      此处管理 AI 会话可用的 MCP 服务，均会合并进每个 AI 会话的 <code>opencode.json</code>。修改后对**新建 / 清空（重置）**的会话生效，已有会话不回改。
    </p>

    <!-- 平台内置 MCP：与外部服务同一张卡片里管理（开关 + 连接健康） -->
    <div class="internal-mcp">
      <div class="internal-mcp__row">
        <el-tag type="success" size="small">内置</el-tag>
        <span class="mono internal-mcp__name">{{ internalMcp.name }}</span>
        <span class="mono internal-mcp__url">{{ internalMcp.url }}</span>
        <el-tag
          v-if="internalHealth"
          :type="internalHealth.ok ? 'success' : 'danger'"
          size="small"
        >
          {{ internalHealth.ok ? `已连接（${internalHealth.latencyMs}ms）` : '连接失败' }}
        </el-tag>
        <el-button size="small" text :loading="healthLoading" @click="checkInternalHealth">
          检测连接
        </el-button>
        <el-switch
          :model-value="internalMcp.enabled"
          style="margin-left: auto"
          @change="(v: string | number | boolean) => toggleInternal(!!v)"
        />
      </div>
      <div class="internal-mcp__desc">
        平台自带 MCP，是 AI 助手数据查询、长期记忆、执行轨迹分析等平台工具的来源。禁用后仅影响之后新建/清空的会话；期间「执行轨迹分析」会拒绝执行并提示。
      </div>
    </div>

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
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listMcpServers, createMcpServer, updateMcpServer, deleteMcpServer,
  getInternalMcpHealth, setInternalMcpEnabled,
  type McpServer, type InternalMcp, type InternalMcpHealth,
} from '@/api/aiMcpServers'

// --- External MCP servers ---------------------------------------------------
const mcpServers = ref<McpServer[]>([])
const mcpLoading = ref(false)
const mcpSaving = ref(false)
const mcpDialog = reactive<{ open: boolean; id: string | null }>({ open: false, id: null })
const mcpForm = reactive({
  name: '', type: 'remote' as 'remote' | 'local', url: '',
  headersText: '', commandText: '', envText: '', enabled: true,
})

// --- Internal (platform) MCP server -----------------------------------------
const internalMcp = ref<InternalMcp>({ name: 'check-manage', url: '', enabled: true })
const internalHealth = ref<InternalMcpHealth | null>(null)
const healthLoading = ref(false)

async function checkInternalHealth() {
  healthLoading.value = true
  try {
    internalHealth.value = await getInternalMcpHealth()
  } catch (e: any) {
    // 网络层失败（502）：后端已探测过，这里把结果标成不可达
    internalHealth.value = { ok: false, error: e?.response?.data?.error || e?.message || '探测失败', url: internalMcp.value.url }
  } finally {
    healthLoading.value = false
  }
}

async function toggleInternal(enabled: boolean) {
  if (!enabled) {
    try {
      await ElMessageBox.confirm(
        '禁用内置 MCP 后，新建/清空的 AI 会话将无法使用数据查询、长期记忆、执行轨迹分析等平台工具。确认禁用？',
        '禁用内置 MCP',
        { confirmButtonText: '禁用', cancelButtonText: '取消', type: 'warning' },
      )
    } catch { return }
  }
  try {
    const r = await setInternalMcpEnabled(enabled)
    internalMcp.value.enabled = r.enabled
    ElMessage.success(enabled ? '已启用，对之后新建/清空的会话生效' : '已禁用，对之后新建/清空的会话生效')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '更新失败')
  }
}

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
  try {
    const r = await listMcpServers()
    mcpServers.value = r.servers
    if (r.internal) internalMcp.value = r.internal
  } catch { /* surfaced */ } finally { mcpLoading.value = false }
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
  loadMcp()
  checkInternalHealth()
})
</script>

<style scoped>
.mcp-card {
  margin-top: 16px;
}

.mcp-card__header {
  display: flex;
  align-items: center;
}

.mcp-card__header h2 {
  margin: 0;
  font-size: 16px;
}

.hint {
  margin-left: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
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

/* 平台内置 MCP 管理节：开关 + 连接健康，位于外部服务表格上方 */
.internal-mcp {
  border: 1px dashed var(--el-border-color);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 14px;
  background: var(--el-fill-color-lighter);
}
.internal-mcp__row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.internal-mcp__name {
  font-weight: 600;
}
.internal-mcp__url {
  color: var(--el-text-color-secondary);
}
.internal-mcp__desc {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}
</style>
