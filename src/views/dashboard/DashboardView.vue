<template>
  <div class="dashboard-view">
    <div class="dashboard-header">
      <div class="dashboard-title">
        <h2>{{ dashboard?.name || '仪表盘' }}</h2>
        <el-select v-model="selectedDashId" placeholder="选择仪表盘" style="width: 200px" @change="loadDashboard">
          <el-option v-for="d in dashboards" :key="d.id" :label="d.name" :value="d.id" />
        </el-select>
      </div>
      <div class="dashboard-actions">
        <el-button type="primary" @click="handleCreate">新建仪表盘</el-button>
        <el-button v-if="dashboard" @click="handleEdit">编辑</el-button>
        <el-button v-if="dashboard" type="danger" @click="handleDelete">删除</el-button>
        <el-button @click="refreshAll"><el-icon><Refresh /></el-icon> 刷新</el-button>
      </div>
    </div>

    <div v-if="dashboard" class="dashboard-grid">
      <div
        v-for="widget in dashboard.layout"
        :key="widget.id"
        class="dashboard-widget"
        :style="widgetStyle(widget)"
      >
        <el-card class="widget-card">
          <template #header>
            <span class="widget-title">{{ widget.title }}</span>
          </template>
          <div class="widget-body">
            <!-- Stat Card -->
            <div v-if="widget.type === 'statCard'" class="stat-card-body">
              <div class="stat-value">{{ widgetData[widget.id]?.value ?? '-' }}</div>
            </div>
            <!-- Chart -->
            <div v-else class="chart-placeholder">
              <div v-if="widgetData[widget.id]?.data" class="simple-chart">
                <div v-for="(item, idx) in widgetData[widget.id].data" :key="idx" class="chart-bar-row">
                  <span class="chart-label">{{ item.key || '(空)' }}</span>
                  <div class="chart-bar-track">
                    <div class="chart-bar-fill" :style="{ width: barWidth(widget.id, item.value) + '%' }" />
                  </div>
                  <span class="chart-value">{{ item.value }}</span>
                </div>
              </div>
              <el-empty v-else :image-size="40" description="暂无数据" />
            </div>
          </div>
        </el-card>
      </div>
    </div>

    <el-empty v-else-if="!loading" description="暂无仪表盘，请新建" />

    <!-- 编辑对话框 -->
    <el-dialog v-model="editDialogVisible" :title="editForm.id ? '编辑仪表盘' : '新建仪表盘'" width="500px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" placeholder="仪表盘名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="全局">
          <el-switch v-model="editForm.isGlobal" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getDashboards, getDashboard, saveDashboard, deleteDashboard, aggregate } from '@/api/dashboard'
import type { Dashboard, DashboardWidget, AggregateResult } from '@/api/dashboard'

const dashboards = ref<Dashboard[]>([])
const dashboard = ref<Dashboard | null>(null)
const selectedDashId = ref('')
const loading = ref(false)
const editDialogVisible = ref(false)
const editForm = reactive({ id: '', name: '', description: '', isGlobal: false })
const widgetData = ref<Record<string, AggregateResult>>({})

async function loadDashboards() {
  try {
    dashboards.value = await getDashboards()
    if (dashboards.value.length > 0 && !selectedDashId.value) {
      selectedDashId.value = dashboards.value[0].id
      await loadDashboard()
    }
  } catch { /* */ }
}

async function loadDashboard() {
  if (!selectedDashId.value) return
  loading.value = true
  try {
    dashboard.value = await getDashboard(selectedDashId.value)
    await refreshAll()
  } catch { /* */ } finally {
    loading.value = false
  }
}

async function refreshAll() {
  if (!dashboard.value) return
  const results: Record<string, AggregateResult> = {}
  await Promise.all(
    dashboard.value.layout.map(async (w) => {
      try {
        results[w.id] = await aggregate({
          collection: w.config.collection,
          metric: w.config.metric,
          field: w.config.field,
          groupField: w.config.groupField,
          filter: w.config.filter,
        })
      } catch { /* */ }
    })
  )
  widgetData.value = results
}

function widgetStyle(w: DashboardWidget) {
  return {
    gridColumn: `${w.x + 1} / span ${w.w}`,
    gridRow: `${w.y + 1} / span ${w.h}`,
  }
}

function barWidth(widgetId: string, value: number): number {
  const data = widgetData.value[widgetId]?.data
  if (!data || data.length === 0) return 0
  const max = Math.max(...data.map(d => d.value), 1)
  return (value / max) * 100
}

function handleCreate() {
  Object.assign(editForm, { id: '', name: '', description: '', isGlobal: false })
  editDialogVisible.value = true
}

function handleEdit() {
  if (!dashboard.value) return
  Object.assign(editForm, {
    id: dashboard.value.id,
    name: dashboard.value.name,
    description: dashboard.value.description || '',
    isGlobal: dashboard.value.isGlobal,
  })
  editDialogVisible.value = true
}

async function handleSave() {
  if (!editForm.name.trim()) {
    ElMessage.warning('请输入名称')
    return
  }
  try {
    const saved = await saveDashboard({
      id: editForm.id || undefined,
      name: editForm.name,
      description: editForm.description,
      isGlobal: editForm.isGlobal,
      layout: editForm.id ? dashboard.value?.layout || [] : [],
    })
    editDialogVisible.value = false
    ElMessage.success('保存成功')
    await loadDashboards()
    if (saved.id) {
      selectedDashId.value = saved.id
      await loadDashboard()
    }
  } catch {
    ElMessage.error('保存失败')
  }
}

async function handleDelete() {
  if (!dashboard.value) return
  try {
    await ElMessageBox.confirm('确定删除此仪表盘？', '确认')
    await deleteDashboard(dashboard.value.id)
    ElMessage.success('已删除')
    dashboard.value = null
    selectedDashId.value = ''
    await loadDashboards()
  } catch { /* */ }
}

onMounted(loadDashboards)
</script>

<style scoped>
.dashboard-view { padding: 0; }
.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.dashboard-title {
  display: flex;
  align-items: center;
  gap: 12px;
}
.dashboard-title h2 { margin: 0; }
.dashboard-actions { display: flex; gap: 8px; }
.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 16px;
  grid-auto-rows: 180px;
}
.widget-card { height: 100%; }
.widget-title { font-size: 14px; font-weight: 500; }
.widget-body { height: calc(100% - 40px); display: flex; align-items: center; justify-content: center; }
.stat-card-body { text-align: center; }
.stat-value { font-size: 36px; font-weight: 700; color: #409EFF; }
.simple-chart { width: 100%; padding: 0 8px; }
.chart-bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.chart-label { flex-shrink: 0; width: 60px; font-size: 12px; color: #606266; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chart-bar-track { flex: 1; height: 16px; background: #f5f7fa; border-radius: 4px; overflow: hidden; }
.chart-bar-fill { height: 100%; background: #409EFF; border-radius: 4px; transition: width 0.3s; }
.chart-value { flex-shrink: 0; width: 40px; font-size: 12px; color: #909399; }
</style>
