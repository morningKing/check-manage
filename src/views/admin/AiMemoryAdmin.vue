<template>
  <div class="memory-admin">
    <el-alert v-if="!config?.mem0Enabled" type="info" show-icon :closable="false"
              class="memory-admin__banner"
              title="长期记忆（mem0）当前未启用——启用后 AI 会话将自动形成并按用户注入长期记忆" />

    <el-tabs v-model="activeTab">
      <!-- ── 配置 ─────────────────────────────────────────────── -->
      <el-tab-pane label="配置" name="config">
        <div v-loading="loadingConfig" class="memory-admin__config">
          <el-form label-width="160px" style="max-width: 720px">
            <el-form-item label="长期记忆 (mem0)">
              <el-switch v-model="form.mem0Enabled" data-test="mem0-switch" />
              <span class="hint">开启后，AI 会话将自动形成并调用按用户的长期记忆</span>
            </el-form-item>

            <template v-if="form.mem0Enabled">
              <el-form-item label="Embedding 提供方">
                <el-radio-group v-model="form.embeddingProvider">
                  <el-radio-button value="api">API 端点</el-radio-button>
                  <el-radio-button value="ollama">本地 Ollama</el-radio-button>
                </el-radio-group>
                <span class="hint" style="margin-left:8px">
                  摘要模型始终走 AI 配置里的 API 端点（{{ config?.endpoint || '未配置' }} · {{ config?.model || '未配置' }}）；
                  切换嵌入提供方/模型后，历史记忆向量维度可能不匹配，届时记忆将暂不可用（需清空记忆数据重建）
                </span>
              </el-form-item>
              <el-form-item label="Embedding 模型" v-if="form.embeddingProvider === 'api'">
                <el-input v-model="form.embeddingModel" placeholder="text-embedding-v3" style="max-width: 320px" />
              </el-form-item>
              <template v-if="form.embeddingProvider === 'ollama'">
                <el-form-item label="Ollama 服务地址">
                  <el-input v-model="form.embeddingOllamaUrl" placeholder="http://localhost:11434" style="max-width: 320px" />
                </el-form-item>
                <el-form-item label="Embedding 模型">
                  <el-input v-model="form.embeddingModel" placeholder="nomic-embed-text" style="max-width: 320px" />
                </el-form-item>
              </template>
              <el-form-item label="向量维度">
                <el-input-number v-model="form.embeddingDims" :min="64" :max="4096" style="width: 180px" />
              </el-form-item>
            </template>

            <el-form-item>
              <el-button type="primary" :loading="savingConfig" data-test="mem0-save"
                         @click="saveConfig">保存配置</el-button>
            </el-form-item>
          </el-form>

          <el-descriptions title="运行状态" :column="1" border size="small" style="max-width: 720px">
            <el-descriptions-item label="底层就绪">
              <el-tag v-if="config?.status?.ready" size="small" type="success">已就绪</el-tag>
              <el-tag v-else size="small" type="info">未就绪（未启用或初始化失败）</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="数据目录">
              <code>{{ config?.status?.storePath }}</code>
              <el-tag v-if="config?.status && !config.status.storeExists" size="small" type="warning"
                      style="margin-left:8px">目录尚未创建</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="摘要模型">
              {{ config?.model || '—' }}（复用 AI 配置的主 API 端点）
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </el-tab-pane>

      <!-- ── 记忆条目 ─────────────────────────────────────────── -->
      <el-tab-pane label="记忆条目" name="entries">
        <div class="memory-admin__toolbar">
          <el-select v-model="entriesOwner" filterable remote :remote-method="searchUsers"
                     :loading="usersLoading" placeholder="选择用户（按用户名搜索）"
                     data-test="entries-user" style="width: 260px" clearable>
            <el-option v-for="u in userOptions" :key="u.id" :label="u.username" :value="u.username" />
          </el-select>
          <el-input v-model="entriesQuery" clearable placeholder="按内容语义搜索（可选）"
                    data-test="entries-query" style="width: 260px" @keyup.enter="loadEntries" />
          <el-button type="primary" :disabled="!entriesOwner" :loading="entriesLoading"
                     data-test="entries-load" @click="loadEntries">查看</el-button>
        </div>

        <el-table :data="entries" v-loading="entriesLoading" size="small" style="width: 100%"
                  data-test="entries-table"
                  :empty-text="entriesOwner ? '该用户暂无记忆（或无匹配）' : '请先选择用户'">
          <el-table-column prop="memory" label="记忆内容" min-width="420" show-overflow-tooltip />
          <el-table-column label="更新时间" width="170">
            <!-- row 默认值兜底：jsdom 下 EP 空表也会以 undefined 调用作用域插槽 -->
            <template #default="{ row = {} }">{{ fmtTime(row.updatedAt || row.createdAt) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row = {} }">
              <el-button link type="danger" data-test="entry-delete"
                         @click="onDeleteEntry(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ── 测试台 ───────────────────────────────────────────── -->
      <el-tab-pane label="测试台" name="testbed">
        <div class="memory-admin__toolbar">
          <el-select v-model="testOwner" filterable remote :remote-method="searchUsers"
                     :loading="usersLoading" placeholder="选择用户（按用户名搜索）"
                     data-test="test-user" style="width: 260px" clearable>
            <el-option v-for="u in userOptions" :key="u.id" :label="u.username" :value="u.username" />
          </el-select>
        </div>

        <el-form inline style="margin-top: 12px">
          <el-form-item label="语义搜索">
            <el-input v-model="testQuery" clearable placeholder="输入查询文本，验证命中与注入效果"
                      data-test="test-query" style="width: 320px" @keyup.enter="runSearch" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :disabled="!testOwner || !testQuery" :loading="searching"
                       data-test="test-search-btn" @click="runSearch">搜索</el-button>
          </el-form-item>
        </el-form>

        <template v-if="searchResult">
          <el-table :data="searchResult.results" size="small" style="width: 100%"
                    empty-text="无命中（该用户暂无相关记忆）">
            <el-table-column type="index" label="#" width="50" />
            <el-table-column prop="memory" label="命中记忆" min-width="420" show-overflow-tooltip />
          </el-table>
          <p class="memory-admin__hint-label">注入提示词原文（模型实际收到的记忆块）：</p>
          <pre class="memory-admin__rendered" data-test="test-rendered">{{ searchResult.rendered || '（无命中，本轮不注入记忆块）' }}</pre>
        </template>

        <el-divider content-position="left">写入测试记忆</el-divider>
        <el-form inline>
          <el-form-item label="内容">
            <el-input v-model="addText" data-test="test-add-text" style="width: 420px"
                      placeholder="例如：用户偏好表格形式的输出" />
          </el-form-item>
          <el-form-item label="原样保存">
            <el-switch v-model="addVerbatim" />
            <span class="hint" style="margin-left:8px">开启 = 不经 LLM 提炼，按原文写入</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :disabled="!testOwner || !addText" :loading="adding"
                       data-test="test-add-btn" @click="onTestAdd">写入</el-button>
          </el-form-item>
        </el-form>
        <p style="color: var(--muted); font-size: 13px;">
          写入后可用上方搜索验证命中；正式的条目清理请到「记忆条目」页签。
        </p>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getMemoryConfig, updateMemoryConfig, searchMemoryUsers,
  listMemoryEntries, deleteMemoryEntry, testMemorySearch, testMemoryAdd,
  type MemoryConfig, type MemoryEntry, type MemoryUser,
  type MemoryTestSearchResult,
} from '@/api/aiMemoryAdmin'

const activeTab = ref<'config' | 'entries' | 'testbed'>('config')

// ── 配置 ──
const loadingConfig = ref(false)
const savingConfig = ref(false)
const config = ref<MemoryConfig | null>(null)
const form = reactive({
  mem0Enabled: false,
  embeddingProvider: 'api' as 'api' | 'ollama',
  embeddingModel: 'text-embedding-v3',
  embeddingOllamaUrl: 'http://localhost:11434',
  embeddingDims: 1024,
})

async function loadConfig() {
  loadingConfig.value = true
  try {
    config.value = await getMemoryConfig()
    form.mem0Enabled = config.value.mem0Enabled
    form.embeddingProvider = config.value.embeddingProvider
    form.embeddingModel = config.value.embeddingModel
    form.embeddingOllamaUrl = config.value.embeddingOllamaUrl
    form.embeddingDims = config.value.embeddingDims
  } finally {
    loadingConfig.value = false
  }
}

async function saveConfig() {
  savingConfig.value = true
  try {
    config.value = await updateMemoryConfig({ ...form })
    ElMessage.success('记忆配置已保存')
  } finally {
    savingConfig.value = false
  }
}

// ── 用户选择器（条目/测试台共用候选列表）──
const userOptions = ref<MemoryUser[]>([])
const usersLoading = ref(false)

async function searchUsers(q: string) {
  if (!q) { userOptions.value = []; return }
  usersLoading.value = true
  try {
    userOptions.value = (await searchMemoryUsers(q)).users
  } finally {
    usersLoading.value = false
  }
}

// ── 记忆条目 ──
const entriesOwner = ref('')
const entriesQuery = ref('')
const entriesLoading = ref(false)
const entries = ref<MemoryEntry[]>([])

function fmtTime(v: string | null | undefined): string {
  return v ? new Date(v).toLocaleString('zh-CN') : '—'
}

async function loadEntries() {
  if (!entriesOwner.value) return
  entriesLoading.value = true
  try {
    const res = await listMemoryEntries(entriesOwner.value, entriesQuery.value || undefined)
    entries.value = res.memories
  } finally {
    entriesLoading.value = false
  }
}

async function onDeleteEntry(row: MemoryEntry) {
  try {
    await ElMessageBox.confirm(
      '删除后该条记忆不可恢复，且不再进入 AI 的记忆注入。确定删除？',
      '删除记忆', { type: 'warning' })
  } catch { return }
  await deleteMemoryEntry(row.id, entriesOwner.value)
  ElMessage.success('已删除')
  await loadEntries()
}

// ── 测试台 ──
const testOwner = ref('')
const testQuery = ref('')
const searching = ref(false)
const searchResult = ref<MemoryTestSearchResult | null>(null)
const addText = ref('')
const addVerbatim = ref(false)
const adding = ref(false)

async function runSearch() {
  if (!testOwner.value || !testQuery.value) return
  searching.value = true
  try {
    searchResult.value = await testMemorySearch(testOwner.value, testQuery.value, 5)
  } finally {
    searching.value = false
  }
}

async function onTestAdd() {
  if (!testOwner.value || !addText.value) return
  const text = addText.value
  const verbatim = addVerbatim.value
  adding.value = true
  try {
    await testMemoryAdd(testOwner.value, text, verbatim)
    ElMessage.success('已写入测试记忆')
    addText.value = ''
    // 写入后直接回查：verbatim 文本用原文查询必命中，提炼文本原样查询通常也命中
    testQuery.value = text
    await runSearch()
  } finally {
    adding.value = false
  }
}

onMounted(loadConfig)
</script>

<style scoped lang="scss">
.memory-admin__banner { margin-bottom: 12px; }
.memory-admin__toolbar { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.memory-admin__config { max-width: 900px; }
.hint { color: var(--muted); font-size: 12px; margin-left: 8px; }
.memory-admin__hint-label { font-size: 13px; color: var(--muted); margin: 12px 0 4px; }
.memory-admin__rendered {
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 40vh;
  overflow: auto;
  background: var(--el-fill-color-light);
  padding: 12px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  margin: 0;
}
</style>
