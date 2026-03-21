/**
 * DashboardView - Kibana 风格仪表盘
 *
 * 功能：
 * - 多仪表盘切换/创建/编辑/删除
 * - 添加/编辑/删除 widget（图表）
 * - 6 种图表类型：指标卡、柱状图、折线图、饼图、面积图、数据表
 * - ECharts 专业渲染
 * - Widget 配置对话框（左侧配置 + 右侧实时预览）
 */
<template>
  <div class="dashboard-view">
    <!-- Header -->
    <div class="dashboard-header">
      <div class="dashboard-title">
        <h2>{{ dashboard?.name || '仪表盘' }}</h2>
        <el-select
          v-model="selectedDashId"
          placeholder="选择仪表盘"
          style="width: 200px"
          @change="loadDashboard"
        >
          <el-option v-for="d in dashboards" :key="d.id" :label="d.name" :value="d.id" />
        </el-select>
      </div>
      <div class="dashboard-actions">
        <el-button type="primary" @click="handleCreateDashboard">
          <el-icon><Plus /></el-icon> 新建仪表盘
        </el-button>
        <template v-if="dashboard">
          <el-button type="success" @click="handleAddWidget">
            <el-icon><PieChart /></el-icon> 添加图表
          </el-button>
          <el-button @click="handleEditDashboard">
            <el-icon><Edit /></el-icon> 编辑
          </el-button>
          <el-button type="danger" @click="handleDeleteDashboard">
            <el-icon><Delete /></el-icon>
          </el-button>
          <el-button @click="refreshAll" :loading="refreshing">
            <el-icon><Refresh /></el-icon>
          </el-button>
        </template>
      </div>
    </div>

    <!-- Widget Grid -->
    <div v-if="dashboard && dashboard.layout.length > 0" class="dashboard-grid">
      <div
        v-for="widget in dashboard.layout"
        :key="widget.id"
        class="dashboard-widget"
        :style="widgetStyle(widget)"
      >
        <el-card class="widget-card" :body-style="{ padding: '8px 12px', height: 'calc(100% - 38px)', overflow: 'hidden' }">
          <template #header>
            <div class="widget-header">
              <span class="widget-title">{{ widget.title }}</span>
              <div class="widget-actions">
                <el-button text size="small" @click="handleEditWidget(widget)">
                  <el-icon><Edit /></el-icon>
                </el-button>
                <el-button text size="small" type="danger" @click="handleDeleteWidget(widget.id)">
                  <el-icon><Close /></el-icon>
                </el-button>
              </div>
            </div>
          </template>

          <div class="widget-body">
            <div v-if="widgetLoading[widget.id]" class="widget-center">
              <el-icon class="is-loading" :size="20"><Loading /></el-icon>
            </div>
            <template v-else-if="widgetData[widget.id]">
              <!-- Metric Card -->
              <MetricCard
                v-if="widget.type === 'metric'"
                :value="widgetData[widget.id]?.value ?? 0"
                :subtitle="getMetricLabel(widget)"
              />
              <!-- Data Table -->
              <DataTableWidget
                v-else-if="widget.type === 'dataTable'"
                :data="widgetData[widget.id]?.data || []"
                :key-label="getGroupLabel(widget)"
                :value-label="getMetricLabel(widget)"
              />
              <!-- Chart (bar/line/pie/area) -->
              <ChartRenderer
                v-else
                :type="widget.type"
                :data="widgetData[widget.id]?.data || []"
              />
            </template>
            <el-empty v-else :image-size="40" description="暂无数据" />
          </div>
        </el-card>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else-if="dashboard && dashboard.layout.length === 0" class="empty-dashboard">
      <el-empty description="暂无图表">
        <el-button type="primary" @click="handleAddWidget">
          <el-icon><PieChart /></el-icon> 添加第一个图表
        </el-button>
      </el-empty>
    </div>

    <el-empty v-else-if="!loading" description="暂无仪表盘，请新建" />

    <!-- Dashboard meta dialog -->
    <el-dialog v-model="metaDialogVisible" :title="metaForm.id ? '编辑仪表盘' : '新建仪表盘'" width="450px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="metaForm.name" placeholder="仪表盘名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="metaForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="全局可见">
          <el-switch v-model="metaForm.isGlobal" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="metaDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSaveDashboard">保存</el-button>
      </template>
    </el-dialog>

    <!-- Widget editor dialog -->
    <WidgetEditor
      v-model="widgetEditorVisible"
      :widget="editingWidget"
      @confirm="handleWidgetConfirm"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Plus, Edit, Delete, Refresh, Close, Loading, PieChart } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getDashboards, getDashboard, saveDashboard, deleteDashboard, aggregate,
  METRIC_TYPE_OPTIONS,
} from '@/api/dashboard'
import type { Dashboard, DashboardWidget, AggregateResult } from '@/api/dashboard'
import MetricCard from '@/components/dashboard/MetricCard.vue'
import DataTableWidget from '@/components/dashboard/DataTableWidget.vue'
import ChartRenderer from '@/components/dashboard/ChartRenderer.vue'
import WidgetEditor from '@/components/dashboard/WidgetEditor.vue'
import { usePageConfigStore } from '@/stores/pageConfig'

const pageConfigStore = usePageConfigStore()

// ==================== State ====================

const dashboards = ref<Dashboard[]>([])
const dashboard = ref<Dashboard | null>(null)
const selectedDashId = ref('')
const loading = ref(false)
const refreshing = ref(false)

const widgetData = ref<Record<string, AggregateResult>>({})
const widgetLoading = ref<Record<string, boolean>>({})

const metaDialogVisible = ref(false)
const metaForm = reactive({ id: '', name: '', description: '', isGlobal: false })

const widgetEditorVisible = ref(false)
const editingWidget = ref<DashboardWidget | null>(null)

// ==================== Data loading ====================

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
  refreshing.value = true
  const results: Record<string, AggregateResult> = {}
  const loadingState: Record<string, boolean> = {}

  dashboard.value.layout.forEach(w => { loadingState[w.id] = true })
  widgetLoading.value = loadingState

  await Promise.all(
    dashboard.value.layout.map(async (w) => {
      try {
        const cfg = w.config
        results[w.id] = await aggregate({
          collection: cfg.collection,
          metrics: cfg.metrics,
          groupBy: cfg.groupBy,
          filter: cfg.filter,
          sort: cfg.sort,
          limit: cfg.limit,
          // Legacy compat
          metric: cfg.metrics?.[0]?.type,
          field: cfg.metrics?.[0]?.field,
          groupField: cfg.groupBy?.field,
        })
      } catch { /* */ } finally {
        widgetLoading.value = { ...widgetLoading.value, [w.id]: false }
      }
    })
  )
  widgetData.value = results
  refreshing.value = false
}

// ==================== Helpers ====================

function widgetStyle(w: DashboardWidget) {
  return {
    gridColumn: `span ${w.w || 6}`,
    gridRow: `span ${w.h || 2}`,
  }
}

function getMetricLabel(w: DashboardWidget): string {
  const mt = w.config.metrics?.[0]?.type || 'count'
  return METRIC_TYPE_OPTIONS.find(m => m.value === mt)?.label || mt
}

function getGroupLabel(w: DashboardWidget): string {
  const gf = w.config.groupBy?.field
  if (!gf) return '分组'
  const pc = pageConfigStore.pageConfigs.find(p => p.id === `page-${w.config.collection}`)
  const field = pc?.fields?.find((f: any) => f.fieldName === gf)
  return field?.label || gf
}

// ==================== Dashboard CRUD ====================

function handleCreateDashboard() {
  Object.assign(metaForm, { id: '', name: '', description: '', isGlobal: false })
  metaDialogVisible.value = true
}

function handleEditDashboard() {
  if (!dashboard.value) return
  Object.assign(metaForm, {
    id: dashboard.value.id,
    name: dashboard.value.name,
    description: dashboard.value.description || '',
    isGlobal: dashboard.value.isGlobal,
  })
  metaDialogVisible.value = true
}

async function handleSaveDashboard() {
  if (!metaForm.name.trim()) {
    ElMessage.warning('请输入名称')
    return
  }
  try {
    const saved = await saveDashboard({
      id: metaForm.id || undefined,
      name: metaForm.name,
      description: metaForm.description,
      isGlobal: metaForm.isGlobal,
      layout: metaForm.id ? dashboard.value?.layout || [] : [],
    })
    metaDialogVisible.value = false
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

async function handleDeleteDashboard() {
  if (!dashboard.value) return
  try {
    await ElMessageBox.confirm('确定删除此仪表盘？', '确认', { type: 'warning' })
    await deleteDashboard(dashboard.value.id)
    ElMessage.success('已删除')
    dashboard.value = null
    selectedDashId.value = ''
    await loadDashboards()
  } catch { /* cancel */ }
}

// ==================== Widget CRUD ====================

function handleAddWidget() {
  editingWidget.value = null
  widgetEditorVisible.value = true
}

function handleEditWidget(widget: DashboardWidget) {
  editingWidget.value = widget
  widgetEditorVisible.value = true
}

async function handleDeleteWidget(widgetId: string) {
  if (!dashboard.value) return
  try {
    await ElMessageBox.confirm('确定删除此图表？', '确认', { type: 'warning' })
    dashboard.value.layout = dashboard.value.layout.filter(w => w.id !== widgetId)
    await saveDashboard(dashboard.value)
    delete widgetData.value[widgetId]
  } catch { /* cancel */ }
}

async function handleWidgetConfirm(widget: DashboardWidget) {
  if (!dashboard.value) return

  const idx = dashboard.value.layout.findIndex(w => w.id === widget.id)
  if (idx >= 0) {
    dashboard.value.layout[idx] = widget
  } else {
    // Auto-flow layout: x/y not used for rendering, just append
    widget.x = 0
    widget.y = 0
    dashboard.value.layout.push(widget)
  }

  await saveDashboard(dashboard.value)

  // Load data for this widget
  try {
    const cfg = widget.config
    widgetData.value[widget.id] = await aggregate({
      collection: cfg.collection,
      metrics: cfg.metrics,
      groupBy: cfg.groupBy,
      filter: cfg.filter,
      sort: cfg.sort,
      limit: cfg.limit,
      metric: cfg.metrics?.[0]?.type,
      field: cfg.metrics?.[0]?.field,
      groupField: cfg.groupBy?.field,
    })
  } catch { /* */ }
}

// ==================== Init ====================

onMounted(async () => {
  if (!pageConfigStore.pageConfigs.length) {
    await pageConfigStore.fetchPageConfigs()
  }
  await loadDashboards()
})
</script>

<style scoped lang="scss">
.dashboard-view {
  padding: 0;
}

.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 8px;
}

.dashboard-title {
  display: flex;
  align-items: center;
  gap: 12px;

  h2 { margin: 0; font-size: 18px; }
}

.dashboard-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 12px;
  grid-auto-rows: 140px;
  grid-auto-flow: dense;
}

.widget-card {
  height: 100%;
  overflow: hidden;

  :deep(.el-card__header) {
    padding: 6px 12px;
    border-bottom: 1px solid #ebeef5;
    line-height: 1;
  }
}

.widget-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.widget-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.widget-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.2s;
}

.widget-card:hover .widget-actions {
  opacity: 1;
}

.widget-body {
  height: 100%;
}

.widget-center {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.empty-dashboard {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 400px;
}

.is-loading {
  animation: spin 1.5s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
