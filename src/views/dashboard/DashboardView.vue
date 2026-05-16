<template>
  <div class="dashboard-view">
    <div class="dashboard-header">
      <div class="dashboard-title">
        <h2>{{ dashboard?.name || '仪表盘' }}</h2>
        <el-select
          v-model="selectedDashId"
          placeholder="选择仪表盘"
          style="width: 220px"
          @change="loadDashboard"
        >
          <el-option v-for="item in dashboards" :key="item.id" :label="item.name" :value="item.id" />
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
            <div v-if="widgetLoading[widget.id]" class="widget-loading-overlay">
              <el-icon class="is-loading" :size="20"><Loading /></el-icon>
            </div>
            <template v-if="widgetData[widget.id]">
              <MetricCard
                v-if="widget.type === 'metric'"
                :value="getSingleValue(widgetData[widget.id])"
                :subtitle="getPrimaryMetricLabel(widget)"
              />
              <DataTableWidget
                v-else-if="widget.type === 'dataTable'"
                :result="widgetData[widget.id]"
                :group-label="getGroupLabel(widget)"
                :column-label="getBreakdownLabel(widget)"
                :metric-labels="getMetricLabelMap(widget)"
                :value-label="getPrimaryMetricLabel(widget)"
              />
              <ChartRenderer
                v-else
                :type="widget.type"
                :result="widgetData[widget.id]"
                :metric-key="shouldShowAllMetrics(widget) ? undefined : getPrimaryMetricKey(widget)"
              />
            </template>
            <el-empty v-else :image-size="40" description="暂无数据" />
          </div>
        </el-card>
      </div>
    </div>

    <div v-else-if="dashboard && dashboard.layout.length === 0" class="empty-dashboard">
      <el-empty description="暂无图表">
        <el-button type="primary" @click="handleAddWidget">
          <el-icon><PieChart /></el-icon> 添加第一个图表
        </el-button>
      </el-empty>
    </div>

    <el-empty v-else-if="!loading" description="暂无仪表盘，请先新建" />

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

    <WidgetEditor
      v-model="widgetEditorVisible"
      :widget="editingWidget"
      @confirm="handleWidgetConfirm"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Close, Delete, Edit, Loading, PieChart, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  aggregate,
  deleteDashboard,
  getDashboard,
  getDashboards,
  getMetricName,
  METRIC_TYPE_OPTIONS,
  saveDashboard,
} from '@/api/dashboard'
import type { AggregateResult, Dashboard, DashboardWidget, MetricDef } from '@/api/dashboard'
import DataTableWidget from '@/components/dashboard/DataTableWidget.vue'
import ChartRenderer from '@/components/dashboard/ChartRenderer.vue'
import MetricCard from '@/components/dashboard/MetricCard.vue'
import WidgetEditor from '@/components/dashboard/WidgetEditor.vue'
import { usePageConfigStore } from '@/stores/pageConfig'

const pageConfigStore = usePageConfigStore()

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

async function loadDashboards() {
  try {
    dashboards.value = await getDashboards()
    if (!dashboards.value.length) {
      dashboard.value = null
      selectedDashId.value = ''
      return
    }
    if (!selectedDashId.value || !dashboards.value.some(item => item.id === selectedDashId.value)) {
      selectedDashId.value = dashboards.value[0].id
    }
    await loadDashboard()
  } catch {
    ElMessage.error('加载仪表盘失败')
  }
}

async function loadDashboard() {
  if (!selectedDashId.value) return
  loading.value = true
  try {
    dashboard.value = await getDashboard(selectedDashId.value)
    await refreshAll()
  } catch {
    ElMessage.error('加载仪表盘详情失败')
  } finally {
    loading.value = false
  }
}

async function loadWidgetData(widget: DashboardWidget) {
  const config = widget.config
  return aggregate({
    collection: config.collection,
    metrics: config.metrics,
    groupBy: config.groupBy,
    breakdownBy: config.breakdownBy,
    filter: config.filter,
    sort: config.sort,
    limit: config.limit,
    metric: config.metrics?.[0]?.type,
    field: config.metrics?.[0]?.field,
    groupField: config.groupBy?.field,
  })
}

async function refreshAll() {
  if (!dashboard.value) return
  refreshing.value = true

  // 只对已有数据的 widget 显示 loading 覆盖层
  const nextLoading: Record<string, boolean> = {}
  dashboard.value.layout.forEach(widget => {
    // 只有已有数据的 widget 才显示 loading 覆盖层
    nextLoading[widget.id] = !!widgetData.value[widget.id]
  })
  widgetLoading.value = nextLoading

  const results: Record<string, AggregateResult> = {}
  await Promise.all(dashboard.value.layout.map(async (widget) => {
    try {
      results[widget.id] = await loadWidgetData(widget)
    } catch {
      ElMessage.error(`图表”${widget.title}”数据加载失败`)
    }
  }))

  // 一次性更新数据和 loading 状态
  widgetData.value = results
  widgetLoading.value = {}
  refreshing.value = false
}

function widgetStyle(widget: DashboardWidget) {
  return {
    gridColumn: `span ${widget.w || 6}`,
    gridRow: `span ${widget.h || 2}`,
  }
}

function findFieldLabel(collection: string, fieldName?: string, fallback = '值') {
  if (!fieldName) return fallback
  const pageId = `page-${collection}`
  const page = pageConfigStore.pageConfigs.find(item => item.id === pageId)
  const field = page?.fields?.find((item: any) => item.fieldName === fieldName)
  return field?.label || fieldName
}

function formatMetricLabel(widget: DashboardWidget, metric?: MetricDef) {
  if (!metric) return '值'
  const metricTypeLabel = METRIC_TYPE_OPTIONS.find(item => item.value === metric.type)?.label || metric.type
  if (metric.type === 'count' || !metric.field) return metricTypeLabel
  return `${metricTypeLabel}(${findFieldLabel(widget.config.collection, metric.field, metric.field)})`
}

function getMetricLabelMap(widget: DashboardWidget) {
  return Object.fromEntries((widget.config.metrics || []).map(metric => {
    const metricName = getMetricName(metric) || metric.type
    return [metricName, formatMetricLabel(widget, metric)]
  }))
}

function getPrimaryMetricKey(widget: DashboardWidget) {
  return getMetricName(widget.config.metrics?.[0])
}

function shouldShowAllMetrics(widget: DashboardWidget) {
  // For bar/line/area charts with multiple metrics, show all as separate series
  const multiSeriesTypes = ['bar', 'line', 'area']
  return multiSeriesTypes.includes(widget.type) && (widget.config.metrics?.length || 0) > 1
}

function getPrimaryMetricLabel(widget: DashboardWidget) {
  return formatMetricLabel(widget, widget.config.metrics?.[0])
}

function getGroupLabel(widget: DashboardWidget) {
  return findFieldLabel(widget.config.collection, widget.config.groupBy?.field, '分组')
}

function getBreakdownLabel(widget: DashboardWidget) {
  return findFieldLabel(widget.config.collection, widget.config.breakdownBy?.field, '系列')
}

function getSingleValue(result?: AggregateResult | null) {
  return result?.type === 'single' ? result.value ?? 0 : 0
}

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
    ElMessage.warning('请输入仪表盘名称')
    return
  }

  try {
    const saved = await saveDashboard({
      id: metaForm.id || undefined,
      name: metaForm.name.trim(),
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
    await ElMessageBox.confirm('确定删除当前仪表盘？', '确认', { type: 'warning' })
    await deleteDashboard(dashboard.value.id)
    ElMessage.success('已删除')
    dashboard.value = null
    selectedDashId.value = ''
    widgetData.value = {}
    await loadDashboards()
  } catch {
    // ignore cancel
  }
}

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
    await ElMessageBox.confirm('确定删除这个图表？', '确认', { type: 'warning' })
    dashboard.value.layout = dashboard.value.layout.filter(item => item.id !== widgetId)
    await saveDashboard(dashboard.value)

    const nextData = { ...widgetData.value }
    delete nextData[widgetId]
    widgetData.value = nextData
  } catch {
    // ignore cancel
  }
}

async function handleWidgetConfirm(widget: DashboardWidget) {
  if (!dashboard.value) return

  const index = dashboard.value.layout.findIndex(item => item.id === widget.id)
  if (index >= 0) {
    dashboard.value.layout[index] = widget
  } else {
    widget.x = 0
    widget.y = 0
    dashboard.value.layout.push(widget)
  }

  await saveDashboard(dashboard.value)

  try {
    widgetData.value = {
      ...widgetData.value,
      [widget.id]: await loadWidgetData(widget),
    }
  } catch {
    ElMessage.error('图表数据加载失败')
  }
}

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
  height: 100%;
  overflow-y: auto;
}

.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 8px;
  padding: 16px 16px 0;
}

.dashboard-title {
  display: flex;
  align-items: center;
  gap: 12px;

  h2 {
    margin: 0;
    font-size: 18px;
  }
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
  padding: 0 16px 16px;
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
  position: relative;
}

.widget-loading-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.7);
  z-index: 10;
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
