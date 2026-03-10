<template>
  <div class="trigger-rule-manager">
    <div class="page-header">
      <h2>联动规则</h2>
      <el-button type="primary" @click="handleAdd">新增规则</el-button>
    </div>

    <el-table :data="rules" border stripe>
      <el-table-column prop="name" label="名称" width="180" />
      <el-table-column prop="sourceCollection" label="源集合" width="150" />
      <el-table-column prop="triggerEvent" label="触发事件" width="120">
        <template #default="{ row }">
          <el-tag size="small">{{ eventLabels[row.triggerEvent] || row.triggerEvent }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="targetCollection" label="目标集合" width="150" />
      <el-table-column prop="actionType" label="动作类型" width="120">
        <template #default="{ row }">
          <el-tag size="small" type="warning">{{ actionLabels[row.actionType] || row.actionType }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="enabled" label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
            {{ row.enabled ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button link @click="handleEdit(row)">编辑</el-button>
          <el-button link @click="handleToggle(row)">{{ row.enabled ? '禁用' : '启用' }}</el-button>
          <el-button link @click="handleViewLogs(row)">日志</el-button>
          <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editVisible" :title="editForm.id ? '编辑规则' : '新增规则'" width="600px">
      <el-form label-width="100px">
        <el-form-item label="规则名称">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="源集合">
          <el-input v-model="editForm.sourceCollection" placeholder="如: tasks" />
        </el-form-item>
        <el-form-item label="触发事件">
          <el-select v-model="editForm.triggerEvent">
            <el-option label="创建" value="create" />
            <el-option label="更新" value="update" />
            <el-option label="删除" value="delete" />
            <el-option label="字段变更" value="fieldChange" />
          </el-select>
        </el-form-item>
        <el-form-item label="触发条件">
          <el-input v-model="conditionJson" type="textarea" :rows="2" placeholder='{"field":"status","value":"done"}' />
        </el-form-item>
        <el-form-item label="目标集合">
          <el-input v-model="editForm.targetCollection" placeholder="如: logs" />
        </el-form-item>
        <el-form-item label="动作类型">
          <el-select v-model="editForm.actionType">
            <el-option label="创建记录" value="create" />
            <el-option label="更新记录" value="update" />
            <el-option label="运行脚本" value="runScript" />
          </el-select>
        </el-form-item>
        <el-form-item label="动作配置">
          <el-input v-model="actionConfigJson" type="textarea" :rows="4" placeholder='{"fieldMapping":{"name":"$source.title"}}' />
        </el-form-item>
        <el-form-item label="执行顺序">
          <el-input-number v-model="editForm.executionOrder" :min="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 日志对话框 -->
    <el-dialog v-model="logVisible" title="执行日志" width="700px">
      <el-table :data="logs" border size="small">
        <el-table-column prop="createdAt" label="时间" width="170" />
        <el-table-column prop="status" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="sourceRecordId" label="源记录" width="150" />
        <el-table-column prop="errorMessage" label="错误信息" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post, put, del } from '@/utils/request'

interface TriggerRule {
  id: string; name: string; description: string; enabled: boolean
  sourceCollection: string; triggerEvent: string; triggerCondition: any
  targetCollection: string; actionType: string; actionConfig: any
  executionOrder: number
}

const eventLabels: Record<string, string> = { create: '创建', update: '更新', delete: '删除', fieldChange: '字段变更' }
const actionLabels: Record<string, string> = { create: '创建记录', update: '更新记录', runScript: '运行脚本' }

const rules = ref<TriggerRule[]>([])
const editVisible = ref(false)
const logVisible = ref(false)
const logs = ref<any[]>([])
const conditionJson = ref('')
const actionConfigJson = ref('')
const editForm = reactive({
  id: '', name: '', description: '', sourceCollection: '',
  triggerEvent: 'update', targetCollection: '', actionType: 'create',
  executionOrder: 0,
})

async function loadRules() {
  try { rules.value = await get('/triggerRules') } catch { /* */ }
}

function handleAdd() {
  Object.assign(editForm, { id: '', name: '', description: '', sourceCollection: '', triggerEvent: 'update', targetCollection: '', actionType: 'create', executionOrder: 0 })
  conditionJson.value = '{}'; actionConfigJson.value = '{}'
  editVisible.value = true
}

function handleEdit(row: TriggerRule) {
  Object.assign(editForm, { id: row.id, name: row.name, description: row.description || '', sourceCollection: row.sourceCollection, triggerEvent: row.triggerEvent, targetCollection: row.targetCollection, actionType: row.actionType, executionOrder: row.executionOrder })
  conditionJson.value = JSON.stringify(row.triggerCondition || {}, null, 2)
  actionConfigJson.value = JSON.stringify(row.actionConfig || {}, null, 2)
  editVisible.value = true
}

async function handleSave() {
  let triggerCondition = {}, actionConfig = {}
  try { triggerCondition = JSON.parse(conditionJson.value || '{}') } catch { ElMessage.error('触发条件 JSON 格式错误'); return }
  try { actionConfig = JSON.parse(actionConfigJson.value || '{}') } catch { ElMessage.error('动作配置 JSON 格式错误'); return }
  const payload = { ...editForm, triggerCondition, actionConfig }
  try {
    if (editForm.id) { await put(`/triggerRules/${editForm.id}`, payload) }
    else { await post('/triggerRules', payload) }
    editVisible.value = false; ElMessage.success('保存成功'); await loadRules()
  } catch { ElMessage.error('保存失败') }
}

async function handleToggle(row: TriggerRule) {
  try { await put(`/triggerRules/${row.id}`, { enabled: !row.enabled }); await loadRules() } catch { /* */ }
}

async function handleDelete(row: TriggerRule) {
  try {
    await ElMessageBox.confirm(`确定删除规则「${row.name}」？`, '确认')
    await del(`/triggerRules/${row.id}`); ElMessage.success('已删除'); await loadRules()
  } catch { /* */ }
}

async function handleViewLogs(row: TriggerRule) {
  try { logs.value = await get(`/triggerRules/${row.id}/logs`); logVisible.value = true } catch { /* */ }
}

onMounted(loadRules)
</script>

<style scoped>
.trigger-rule-manager { padding: 0; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.page-header h2 { margin: 0; }
</style>
