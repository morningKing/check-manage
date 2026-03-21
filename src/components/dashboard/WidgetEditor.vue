/**
 * WidgetEditor - 图表配置对话框
 *
 * 左侧配置面板 + 右侧实时预览，类似 Kibana Lens 编辑器
 */
<template>
  <el-dialog
    v-model="visible"
    :title="isEdit ? '编辑图表' : '添加图表'"
    width="960px"
    top="5vh"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <div class="editor-body">
      <!-- 左侧配置 -->
      <div class="editor-config">
        <!-- 图表类型 -->
        <div class="config-section">
          <div class="config-label">图表类型</div>
          <div class="type-grid">
            <button
              v-for="t in WIDGET_TYPE_OPTIONS"
              :key="t.value"
              class="type-btn"
              :class="{ active: form.type === t.value }"
              @click="form.type = t.value"
            >
              <el-icon :size="20"><component :is="iconMap[t.icon]" /></el-icon>
              <span>{{ t.label }}</span>
            </button>
          </div>
        </div>

        <!-- 数据源 -->
        <div class="config-section">
          <div class="config-label">数据集合</div>
          <el-select
            v-model="form.collection"
            placeholder="选择数据集合"
            filterable
            style="width: 100%"
            @change="onCollectionChange"
          >
            <el-option
              v-for="c in collections"
              :key="c.value"
              :label="c.label"
              :value="c.value"
            />
          </el-select>
        </div>

        <!-- 指标 -->
        <div class="config-section">
          <div class="config-label">指标 (Y 轴)</div>
          <div class="config-row">
            <el-select v-model="form.metricType" style="flex: 1">
              <el-option
                v-for="m in METRIC_TYPE_OPTIONS"
                :key="m.value"
                :label="m.label"
                :value="m.value"
              />
            </el-select>
            <el-select
              v-if="needMetricField"
              v-model="form.metricField"
              placeholder="字段"
              style="flex: 1"
              filterable
            >
              <el-option v-for="f in numericFields" :key="f.value" :label="f.label" :value="f.value" />
            </el-select>
          </div>
        </div>

        <!-- 分组 -->
        <div class="config-section">
          <div class="config-label">
            <el-checkbox v-model="form.enableGroup">分组 (X 轴)</el-checkbox>
          </div>
          <template v-if="form.enableGroup">
            <el-select
              v-model="form.groupField"
              placeholder="分组字段"
              filterable
              style="width: 100%; margin-bottom: 8px"
            >
              <el-option v-for="f in allFields" :key="f.value" :label="f.label" :value="f.value" />
            </el-select>
            <div class="config-row">
              <el-select v-model="form.groupType" style="flex: 1">
                <el-option label="按值分组 (Terms)" value="terms" />
                <el-option label="日期直方图" value="dateHistogram" />
              </el-select>
              <el-select v-if="form.groupType === 'dateHistogram'" v-model="form.interval" style="flex: 1">
                <el-option label="按日" value="day" />
                <el-option label="按周" value="week" />
                <el-option label="按月" value="month" />
                <el-option label="按年" value="year" />
              </el-select>
            </div>
            <div class="config-row">
              <el-select v-model="form.sort" placeholder="排序" style="flex: 1">
                <el-option label="值降序" value="value_desc" />
                <el-option label="值升序" value="value_asc" />
                <el-option label="键升序" value="key_asc" />
                <el-option label="键降序" value="key_desc" />
              </el-select>
              <el-select v-model="form.limit" style="flex: 1">
                <el-option v-for="n in [5, 10, 20, 50, 100]" :key="n" :label="`前 ${n} 条`" :value="n" />
              </el-select>
            </div>
          </template>
        </div>

        <!-- 过滤 -->
        <div class="config-section">
          <div class="config-label">过滤条件</div>
          <div v-for="(f, i) in form.filters" :key="i" class="config-row filter-row">
            <el-select v-model="f.field" placeholder="字段" filterable style="flex: 1">
              <el-option v-for="ff in allFields" :key="ff.value" :label="ff.label" :value="ff.value" />
            </el-select>
            <span class="filter-eq">=</span>
            <el-input v-model="f.value" placeholder="值" style="flex: 1" />
            <el-button text type="danger" @click="form.filters.splice(i, 1)">
              <el-icon><Close /></el-icon>
            </el-button>
          </div>
          <el-button text type="primary" size="small" @click="form.filters.push({ field: '', value: '' })">
            + 添加过滤
          </el-button>
        </div>

        <!-- 显示选项 -->
        <div class="config-section">
          <div class="config-label">显示选项</div>
          <el-input v-model="form.title" placeholder="图表标题" style="margin-bottom: 8px" />
          <div class="config-row">
            <div style="flex: 1">
              <div class="config-sublabel">宽度 (1-12)</div>
              <el-slider v-model="form.w" :min="2" :max="12" :step="1" show-stops />
            </div>
            <div style="flex: 1">
              <div class="config-sublabel">高度 (1-4)</div>
              <el-slider v-model="form.h" :min="1" :max="4" :step="1" show-stops />
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧预览 -->
      <div class="editor-preview">
        <div class="preview-header">
          <span>{{ form.title || '预览' }}</span>
          <el-button size="small" @click="loadPreview" :loading="previewLoading">
            <el-icon><Refresh /></el-icon> 刷新预览
          </el-button>
        </div>
        <div class="preview-body">
          <div v-if="previewLoading" class="preview-center">
            <el-icon class="is-loading" :size="24"><Loading /></el-icon>
          </div>
          <div v-else-if="!previewData" class="preview-center">
            <el-empty :image-size="60" description="配置集合和指标后预览" />
          </div>
          <template v-else>
            <MetricCard
              v-if="form.type === 'metric'"
              :value="previewData.value ?? 0"
              :subtitle="metricSubtitle"
            />
            <DataTableWidget
              v-else-if="form.type === 'dataTable'"
              :data="previewData.data || []"
            />
            <ChartRenderer
              v-else
              :type="form.type"
              :data="previewData.data || []"
            />
          </template>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" @click="handleConfirm">确定</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import {
  Refresh, Loading, Close,
  Odometer, Histogram, TrendCharts, PieChart as PieChartIcon, DataLine, Grid,
} from '@element-plus/icons-vue'
import { aggregate, WIDGET_TYPE_OPTIONS, METRIC_TYPE_OPTIONS } from '@/api/dashboard'
import type { DashboardWidget, WidgetType, AggregateResult } from '@/api/dashboard'
import { usePageConfigStore } from '@/stores/pageConfig'
import { v4 as uuidv4 } from 'uuid'
import MetricCard from './MetricCard.vue'
import DataTableWidget from './DataTableWidget.vue'
import ChartRenderer from './ChartRenderer.vue'

const iconMap: Record<string, any> = {
  Odometer, Histogram, TrendCharts, PieChart: PieChartIcon, DataLine, Grid,
}

interface Props {
  modelValue: boolean
  widget?: DashboardWidget | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'confirm', widget: DashboardWidget): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const isEdit = computed(() => !!props.widget?.id)
const pageConfigStore = usePageConfigStore()

const form = reactive({
  type: 'bar' as WidgetType,
  collection: '',
  metricType: 'count',
  metricField: '',
  enableGroup: true,
  groupField: '',
  groupType: 'terms',
  interval: 'month',
  sort: 'value_desc',
  limit: 10,
  filters: [] as Array<{ field: string; value: string }>,
  title: '',
  w: 6,
  h: 2,
})

const previewData = ref<AggregateResult | null>(null)
const previewLoading = ref(false)

const collections = computed(() => {
  return pageConfigStore.pageConfigs.map(p => ({
    label: p.name,
    value: p.id.replace('page-', ''),
  }))
})

const currentFields = computed(() => {
  const pc = pageConfigStore.pageConfigs.find(
    p => p.id === `page-${form.collection}`
  )
  return pc?.fields || []
})

const allFields = computed(() =>
  currentFields.value
    .filter(f => !f.hidden)
    .map(f => ({ label: f.label, value: f.fieldName }))
)

const numericFields = computed(() =>
  currentFields.value
    .filter(f => f.controlType === 'number' || f.controlType === 'autoSequence')
    .map(f => ({ label: f.label, value: f.fieldName }))
)

const needMetricField = computed(() => {
  return METRIC_TYPE_OPTIONS.find(m => m.value === form.metricType)?.needField ?? false
})

const metricSubtitle = computed(() => {
  const m = METRIC_TYPE_OPTIONS.find(x => x.value === form.metricType)
  return m ? m.label : ''
})

function onCollectionChange() {
  form.groupField = ''
  form.metricField = ''
  previewData.value = null
}

// Auto-preview on config change (debounced)
let previewTimer: ReturnType<typeof setTimeout> | null = null
watch(
  () => [form.collection, form.metricType, form.metricField, form.enableGroup, form.groupField, form.groupType, form.interval, form.sort, form.limit, form.type],
  () => {
    if (previewTimer) clearTimeout(previewTimer)
    previewTimer = setTimeout(loadPreview, 500)
  },
  { deep: true }
)

async function loadPreview() {
  if (!form.collection) return
  previewLoading.value = true
  try {
    const filter: Record<string, any> = {}
    for (const f of form.filters) {
      if (f.field && f.value) filter[f.field] = f.value
    }

    previewData.value = await aggregate({
      collection: form.collection,
      metrics: [{ type: form.metricType as any, field: form.metricField || undefined }],
      groupBy: form.enableGroup && form.groupField ? {
        field: form.groupField,
        type: form.groupType as any,
        interval: form.groupType === 'dateHistogram' ? form.interval as any : undefined,
      } : undefined,
      filter: Object.keys(filter).length > 0 ? filter : undefined,
      sort: form.sort as any,
      limit: form.limit,
    })
  } catch {
    previewData.value = null
  } finally {
    previewLoading.value = false
  }
}

function handleConfirm() {
  if (!form.collection || !form.title) {
    return
  }

  const filter: Record<string, any> = {}
  for (const f of form.filters) {
    if (f.field && f.value) filter[f.field] = f.value
  }

  const widget: DashboardWidget = {
    id: props.widget?.id || `w-${uuidv4().slice(0, 8)}`,
    type: form.type,
    title: form.title,
    x: props.widget?.x ?? 0,
    y: props.widget?.y ?? 0,
    w: form.w,
    h: form.h,
    config: {
      collection: form.collection,
      metrics: [{ type: form.metricType as any, field: form.metricField || undefined }],
      groupBy: form.enableGroup && form.groupField ? {
        field: form.groupField,
        type: form.groupType as any,
        interval: form.groupType === 'dateHistogram' ? form.interval as any : undefined,
      } : undefined,
      filter: Object.keys(filter).length > 0 ? filter : undefined,
      sort: form.sort as any,
      limit: form.limit,
    },
  }

  emit('confirm', widget)
  visible.value = false
}

// Populate form when editing
watch(visible, (v) => {
  if (!v) return
  if (props.widget) {
    const w = props.widget
    const cfg = w.config
    form.type = w.type
    form.collection = cfg.collection
    form.metricType = cfg.metrics?.[0]?.type || 'count'
    form.metricField = cfg.metrics?.[0]?.field || ''
    form.enableGroup = !!cfg.groupBy
    form.groupField = cfg.groupBy?.field || ''
    form.groupType = cfg.groupBy?.type || 'terms'
    form.interval = cfg.groupBy?.interval || 'month'
    form.sort = cfg.sort || 'value_desc'
    form.limit = cfg.limit || 10
    form.filters = cfg.filter ? Object.entries(cfg.filter).map(([field, value]) => ({ field, value: String(value) })) : []
    form.title = w.title
    form.w = w.w
    form.h = w.h
  } else {
    form.type = 'bar'
    form.collection = ''
    form.metricType = 'count'
    form.metricField = ''
    form.enableGroup = true
    form.groupField = ''
    form.groupType = 'terms'
    form.interval = 'month'
    form.sort = 'value_desc'
    form.limit = 10
    form.filters = []
    form.title = ''
    form.w = 6
    form.h = 2
  }
  previewData.value = null

  // 确保 pageConfigs 已加载
  if (!pageConfigStore.pageConfigs.length) {
    pageConfigStore.fetchPageConfigs()
  }
})
</script>

<style scoped lang="scss">
.editor-body {
  display: flex;
  gap: 20px;
  min-height: 520px;
}
.editor-config {
  width: 360px;
  flex-shrink: 0;
  overflow-y: auto;
  max-height: 65vh;
  padding-right: 8px;
}
.editor-preview {
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: hidden;
}
.preview-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: #f5f7fa;
  border-bottom: 1px solid #ebeef5;
  font-weight: 500;
  font-size: 13px;
}
.preview-body {
  flex: 1;
  padding: 12px;
  min-height: 300px;
}
.preview-center {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.config-section {
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #f0f0f0;
}
.config-label {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 8px;
}
.config-sublabel {
  font-size: 12px;
  color: #909399;
  margin-bottom: 2px;
}
.config-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.type-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.type-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 10px 4px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  background: white;
  cursor: pointer;
  font-size: 12px;
  color: #606266;
  transition: all 0.15s;

  &:hover {
    border-color: #409eff;
    color: #409eff;
  }
  &.active {
    border-color: #409eff;
    background: #ecf5ff;
    color: #409eff;
    font-weight: 500;
  }
}

.filter-row {
  align-items: center;
}
.filter-eq {
  color: #909399;
  font-size: 13px;
}

.is-loading {
  animation: spin 1.5s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
