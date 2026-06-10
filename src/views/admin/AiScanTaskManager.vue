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
        <el-form-item label="数据页(collection)"><el-input v-model="form.collection" placeholder="如 inspection-case" /></el-form-item>
        <el-form-item label="分支"><el-input v-model="form.branchId" /></el-form-item>
        <el-form-item label="状态字段"><el-input v-model="form.statusField" placeholder="记录里的字段名，如 审核状态" /></el-form-item>
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
          <el-input v-model="form.agent" placeholder="留空使用 OpenCode 默认 Agent，如 build" style="width:300px" clearable />
          <div class="hint">填写 OpenCode Agent 名称（如 build、review），该任务的所有 AI 会话将使用指定 Agent 执行</div>
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
import type { AiScanTask } from '@/types'

const store = useAiScanTaskStore()
const selectedId = ref('')
const form = ref<AiScanTask | null>(null)
const extraFilterText = ref('{}')
const running = ref(false)

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
