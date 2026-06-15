<template>
  <div class="scan-mgr">
    <el-card class="list-card" v-loading="store.loading">
      <template #header>
        <div class="hd"><span>AI 定时任务</span>
          <el-button type="primary" size="small" @click="openCreate">新建任务</el-button></div>
      </template>
      <el-empty v-if="store.tasks.length === 0" description="暂无任务" :image-size="80" />
      <el-menu v-else :default-active="selectedId" @select="select">
        <el-menu-item v-for="t in store.tasks" :key="t.id" :index="t.id">
          <span>{{ t.name }}</span>
          <el-tag :type="t.enabled ? 'success' : 'info'" size="small" style="margin-left:8px">
            {{ t.enabled ? '启用' : '停用' }}</el-tag>
        </el-menu-item>
      </el-menu>
    </el-card>

    <el-card v-if="form" class="editor-card">
      <template #header>
        <div class="hd"><span>{{ form.name || '任务配置' }}</span>
          <div>
            <el-button size="small" @click="runNow" :loading="running">立即运行</el-button>
            <el-button v-if="form.id" type="danger" size="small" @click="remove">删除</el-button>
            <el-button type="primary" size="small" @click="save">保存</el-button>
          </div>
        </div>
      </template>
      <el-form label-width="120px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
        <el-form-item label="数据页(collection)">
          <el-select v-model="form.collection" filterable placeholder="选择数据页" style="width:300px" @change="onCollectionChange">
            <el-option v-for="c in collectionOptions" :key="c.value" :label="c.label" :value="c.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="分支">
          <el-select v-model="form.branchId" filterable placeholder="选择分支" style="width:300px">
            <el-option v-for="b in branchOptions" :key="b.value" :label="b.label" :value="b.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态字段">
          <el-select v-model="form.statusField" filterable allow-create clearable
                     placeholder="选择或输入字段名" style="width:300px">
            <el-option v-for="f in fieldsOf(form.collection)" :key="f.value" :label="f.label" :value="f.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="待处理值"><el-input v-model="form.pendingValue" placeholder="留空=匹配空/未设置" /></el-form-item>
        <el-form-item label="处理中值"><el-input v-model="form.runningValue" /></el-form-item>
        <el-form-item label="已处理值"><el-input v-model="form.doneValue" /></el-form-item>
        <el-form-item label="失败值"><el-input v-model="form.failedValue" /></el-form-item>
        <el-form-item label="调度间隔(分钟)"><el-input-number v-model="form.scheduleIntervalMinutes" :min="1" /></el-form-item>
        <el-form-item label="每次最多条数"><el-input-number v-model="form.maxRecordsPerScan" :min="1" /></el-form-item>
        <el-form-item label="候选过滤(JSON)">
          <el-input v-model="extraFilterText" type="textarea" :rows="2" placeholder='如 {"优先级":"高"}' />
          <div class="hint">过滤条件使用记录的原始字段名（非显示标签）</div>
        </el-form-item>
        <el-form-item label="提示词">
          <el-input v-model="form.promptTemplate" type="textarea" :rows="5"
            placeholder="操作指令，引用要用的 skill" />
        </el-form-item>
        <el-form-item label="Agent">
          <el-select v-model="form.agent" placeholder="使用 OpenCode 默认 Agent" clearable style="width:300px">
            <el-option v-for="a in agents" :key="a.name" :label="a.name" :value="a.name">
              <span>{{ a.name }}</span>
              <span v-if="a.description" style="color:#909399;font-size:11px;margin-left:6px">{{ a.description }}</span>
            </el-option>
          </el-select>
          <div class="hint">选择后，该任务的所有 AI 会话将使用指定 Agent 执行</div>
        </el-form-item>
        <el-form-item label="字段映射">
          <div v-for="(m, i) in form.fieldMapping" :key="i" class="map-row">
            <el-input v-model="m.jsonKey" placeholder="AI JSON 键" style="width:160px" />
            <span>→</span>
            <el-input v-model="m.column" placeholder="回写到的列" style="width:160px" />
            <el-checkbox v-model="m.required">必填</el-checkbox>
            <el-button link type="danger" @click="form.fieldMapping.splice(i,1)">删除</el-button>
          </div>
          <el-button size="small" @click="form.fieldMapping.push({ jsonKey:'', column:'', required:false })">+ 添加映射</el-button>
        </el-form-item>
        <el-form-item label="输出契约预览">
          <pre class="contract">{{ contractPreview }}</pre>
        </el-form-item>
        <el-form-item label="运行信息" v-if="form.id">
          <div class="hint">上次运行：{{ form.lastRunAt || '从未' }}；本次处理：{{ form.lastScanCount ?? 0 }} 条
            <span v-if="form.lastError" style="color:#f56c6c">；错误：{{ form.lastError }}</span></div>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAiScanTaskStore } from '@/stores/aiScanTask'
import { usePageConfigStore } from '@/stores/pageConfig'
import type { AiScanTask } from '@/types'
import { listAgents } from '@/api/aiChat'
import type { AgentInfo } from '@/api/aiChat'
import { getAllBranches } from '@/api/projectVersion'

const store = useAiScanTaskStore()
const pageConfigStore = usePageConfigStore()
const selectedId = ref('')
const form = ref<AiScanTask | null>(null)
const extraFilterText = ref('{}')
const running = ref(false)
const agents = ref<AgentInfo[]>([])

// ---- 下拉选项：数据页 / 分支 / 状态字段 ----
const collectionOptions = computed(() =>
  pageConfigStore.pageConfigs.map((c) => ({
    label: `${c.name}（${c.id.replace('page-', '')}）`,
    value: c.id.replace('page-', ''),
  })),
)
const branchOptions = ref<{ label: string; value: string }[]>([{ label: '主分支（main）', value: 'main' }])
function fieldsOf(collection?: string): { label: string; value: string }[] {
  if (!collection) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id.replace('page-', '') === collection)
  if (!config) return []
  return config.fields.map((f) => ({ label: `${f.label}（${f.fieldName}）`, value: f.fieldName }))
}
function onCollectionChange() {
  // 切换数据页后，若状态字段已不属于新数据页则清空，避免残留
  if (!form.value) return
  const valid = fieldsOf(form.value.collection).some((f) => f.value === form.value!.statusField)
  if (!valid) form.value.statusField = ''
}

function blank(): AiScanTask {
  return { id: '', name: '', enabled: true, collection: '', branchId: 'main', statusField: '',
    pendingValue: '', runningValue: '处理中', doneValue: '已处理', failedValue: '处理失败',
    extraFilter: {}, contextFields: {}, promptTemplate: '', fieldMapping: [],
    scheduleIntervalMinutes: 15, maxRecordsPerScan: 20, agent: null }
}

const contractPreview = computed(() => {
  const keys = (form.value?.fieldMapping || []).map(m => `"${m.jsonKey}": ...`).join(', ')
  return `完成后请在末尾输出 JSON：\n{ ${keys} }`
})

async function select(id: string) {
  selectedId.value = id
  const t = await store.fetchOne(id)
  form.value = t
  extraFilterText.value = JSON.stringify(t.extraFilter || {}, null, 0)
}

function openCreate() { form.value = blank(); selectedId.value = ''; extraFilterText.value = '{}' }

function parsedFilter(): Record<string, unknown> {
  try { return JSON.parse(extraFilterText.value || '{}') } catch { ElMessage.error('候选过滤 JSON 不合法'); throw new Error('bad json') }
}

async function save() {
  if (!form.value) return
  if (!form.value.name || !form.value.collection || !form.value.statusField || !form.value.promptTemplate) {
    ElMessage.warning('名称/数据页/状态字段/提示词为必填'); return
  }
  const payload = { ...form.value, extraFilter: parsedFilter() }
  if (form.value.id) await store.save(form.value.id, payload)
  else { const t = await store.add(payload); await select(t.id) }
  extraFilterText.value = JSON.stringify(payload.extraFilter)
  ElMessage.success('已保存')
}

async function remove() {
  if (!form.value?.id) return
  try { await ElMessageBox.confirm(`删除任务「${form.value.name}」？`, '确认', { type: 'warning' }) } catch { return }
  await store.remove(form.value.id)
  form.value = null; selectedId.value = ''
  ElMessage.success('已删除')
}

async function runNow() {
  if (!form.value?.id) { ElMessage.warning('请先保存任务'); return }
  running.value = true
  try { const r = await store.runNow(form.value.id); ElMessage.success(r.message); await select(form.value.id) }
  finally { running.value = false }
}

onMounted(async () => {
  await store.load()
  if (store.tasks.length) await select(store.tasks[0].id)
  // 数据页 / 状态字段 下拉来源
  if (!pageConfigStore.pageConfigs.length) {
    try { await pageConfigStore.fetchPageConfigs() } catch { /* non-fatal */ }
  }
  // 分支下拉：主分支 + 所有项目活动分支
  try {
    const branches = await getAllBranches()
    const opts = [{ label: '主分支（main）', value: 'main' }]
    for (const b of branches) {
      if (b.id === 'main') continue
      opts.push({ label: b.projectName ? `${b.name}（${b.projectName}）` : b.name, value: b.id })
    }
    branchOptions.value = opts
  } catch { /* non-fatal，至少保留 main */ }
  try {
    const r = await listAgents()
    agents.value = [...r.agents, ...r.subagents]
  } catch { /* non-fatal */ }
})
</script>

<style scoped lang="scss">
.scan-mgr { display: flex; gap: 16px; height: 100%; }
.list-card { width: 240px; flex-shrink: 0; }
.editor-card { flex: 1; overflow: auto; }
.hd { display: flex; justify-content: space-between; align-items: center; }
.map-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.contract { background: #f5f7fa; padding: 8px; border-radius: 4px; font-size: 12px; white-space: pre-wrap; }
.hint { color: #909399; font-size: 12px; }
</style>
