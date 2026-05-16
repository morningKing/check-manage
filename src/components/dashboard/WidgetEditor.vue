<template>
  <el-dialog
    v-model="visible"
    :title="isEdit ? '编辑图表' : '添加图表'"
    width="1040px"
    top="5vh"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <div class="editor-body">
      <div class="editor-config">
        <div class="config-section">
          <div class="config-label">图表类型</div>
          <div class="type-grid">
            <button
              v-for="item in WIDGET_TYPE_OPTIONS"
              :key="item.value"
              class="type-btn"
              :class="{ active: form.type === item.value }"
              @click="form.type = item.value"
            >
              <el-icon :size="20"><component :is="iconMap[item.icon]" /></el-icon>
              <span>{{ item.label }}</span>
            </button>
          </div>
        </div>

        <div class="config-section">
          <div class="config-label">数据集合</div>
          <el-select
            v-model="form.collection"
            placeholder="选择数据集合"
            filterable
            style="width: 100%"
            @change="onCollectionChange"
          >
            <el-option v-for="item in collections" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </div>

        <div class="config-section">
          <div class="config-label">指标</div>
          <div class="config-row">
            <el-select v-model="form.metricType" style="flex: 1">
              <el-option v-for="item in METRIC_TYPE_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-select
              v-if="needMetricField"
              v-model="form.metricField"
              placeholder="字段"
              style="flex: 1"
              filterable
            >
              <el-option v-for="item in metricFields" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </div>
        </div>

        <div class="config-section">
          <div class="config-label">
            <el-checkbox v-model="form.enableGroup" :disabled="!supportsGrouping">主分组</el-checkbox>
          </div>
          <template v-if="form.enableGroup && supportsGrouping">
            <el-select
              v-model="form.groupField"
              placeholder="选择主分组字段"
              filterable
              style="width: 100%; margin-bottom: 8px"
            >
              <el-option v-for="item in groupFieldOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <div class="config-row">
              <el-select v-model="form.groupType" style="flex: 1">
                <el-option label="按值分组" value="terms" />
                <el-option label="日期直方图" value="dateHistogram" />
                <el-option label="数值直方图" value="histogram" />
                <el-option label="数值区间" value="range" />
                <el-option label="是否为空" value="exists" />
              </el-select>
              <el-select v-if="form.groupType === 'dateHistogram'" v-model="form.groupDateInterval" style="flex: 1">
                <el-option label="按日" value="day" />
                <el-option label="按周" value="week" />
                <el-option label="按月" value="month" />
                <el-option label="按年" value="year" />
              </el-select>
              <el-input-number
                v-else-if="form.groupType === 'histogram'"
                v-model="form.groupHistogramInterval"
                :min="1"
                :step="1"
                style="flex: 1"
              />
            </div>
            <div v-if="form.groupType === 'range'" class="range-list">
              <div v-for="(item, index) in form.groupRanges" :key="`group-${index}`" class="config-row range-row">
                <el-input v-model="item.key" placeholder="区间名称" style="flex: 1" />
                <el-input v-model="item.from" placeholder="起始值" style="flex: 1" />
                <el-input v-model="item.to" placeholder="结束值" style="flex: 1" />
                <el-button text type="danger" @click="removeRange('group', index)">
                  <el-icon><Close /></el-icon>
                </el-button>
              </div>
              <el-button text type="primary" size="small" @click="addRange('group')">+ 添加区间</el-button>
            </div>
            <div class="config-row">
              <el-select v-model="form.sort" placeholder="排序" style="flex: 1">
                <el-option label="值降序" value="value_desc" />
                <el-option label="值升序" value="value_asc" />
                <el-option label="键升序" value="key_asc" />
                <el-option label="键降序" value="key_desc" />
              </el-select>
              <el-select v-model="form.limit" style="flex: 1">
                <el-option v-for="size in [5, 10, 20, 50, 100]" :key="size" :label="`前 ${size} 条`" :value="size" />
              </el-select>
            </div>
          </template>
        </div>

        <div v-if="supportsBreakdown && form.enableGroup" class="config-section">
          <div class="config-label">
            <el-checkbox v-model="form.enableBreakdown">次分组 / 系列拆分</el-checkbox>
          </div>
          <template v-if="form.enableBreakdown">
            <el-select
              v-model="form.breakdownField"
              placeholder="选择次分组字段"
              filterable
              style="width: 100%; margin-bottom: 8px"
            >
              <el-option v-for="item in breakdownFieldOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <div class="config-row">
              <el-select v-model="form.breakdownType" style="flex: 1">
                <el-option label="按值分组" value="terms" />
                <el-option label="日期直方图" value="dateHistogram" />
                <el-option label="数值直方图" value="histogram" />
                <el-option label="数值区间" value="range" />
                <el-option label="是否为空" value="exists" />
              </el-select>
              <el-select v-if="form.breakdownType === 'dateHistogram'" v-model="form.breakdownDateInterval" style="flex: 1">
                <el-option label="按日" value="day" />
                <el-option label="按周" value="week" />
                <el-option label="按月" value="month" />
                <el-option label="按年" value="year" />
              </el-select>
              <el-input-number
                v-else-if="form.breakdownType === 'histogram'"
                v-model="form.breakdownHistogramInterval"
                :min="1"
                :step="1"
                style="flex: 1"
              />
            </div>
            <div v-if="form.breakdownType === 'range'" class="range-list">
              <div v-for="(item, index) in form.breakdownRanges" :key="`breakdown-${index}`" class="config-row range-row">
                <el-input v-model="item.key" placeholder="区间名称" style="flex: 1" />
                <el-input v-model="item.from" placeholder="起始值" style="flex: 1" />
                <el-input v-model="item.to" placeholder="结束值" style="flex: 1" />
                <el-button text type="danger" @click="removeRange('breakdown', index)">
                  <el-icon><Close /></el-icon>
                </el-button>
              </div>
              <el-button text type="primary" size="small" @click="addRange('breakdown')">+ 添加区间</el-button>
            </div>
          </template>
        </div>

        <div class="config-section">
          <div class="config-label">过滤条件</div>
          <div v-for="(item, index) in form.filters" :key="index" class="config-row filter-row">
            <el-select v-model="item.field" placeholder="字段" filterable style="flex: 1">
              <el-option v-for="field in allFields" :key="field.value" :label="field.label" :value="field.value" />
            </el-select>
            <span class="filter-eq">=</span>
            <el-input v-model="item.value" placeholder="值" style="flex: 1" />
            <el-button text type="danger" @click="form.filters.splice(index, 1)">
              <el-icon><Close /></el-icon>
            </el-button>
          </div>
          <el-button text type="primary" size="small" @click="form.filters.push({ field: '', value: '' })">+ 添加过滤</el-button>
        </div>

        <div class="config-section">
          <div class="config-label">显示选项</div>
          <el-input v-model="form.title" placeholder="图表标题" style="margin-bottom: 8px" />

          <!-- Gauge config -->
          <div v-if="form.type === 'gauge'" class="config-row" style="margin-bottom: 8px">
            <el-input-number v-model="form.gaugeTarget" :min="1" :max="10000" placeholder="目标值" style="flex: 1" />
            <span class="config-sublabel" style="flex: 1; padding-left: 8px">目标值（默认100）</span>
          </div>

          <!-- Funnel config -->
          <div v-if="form.type === 'funnel'" style="margin-bottom: 8px">
            <el-checkbox v-model="form.funnelShowRate">显示转化率百分比</el-checkbox>
          </div>

          <!-- Radar config hint -->
          <div v-if="form.type === 'radar'" style="margin-bottom: 8px; color: #909399; font-size: 12px">
            雷达图将使用所有配置的指标作为维度，请添加多个指标
          </div>

          <!-- Ring config hint -->
          <div v-if="form.type === 'ring'" style="margin-bottom: 8px; color: #909399; font-size: 12px">
            环形图中间显示各分组值的总和
          </div>

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
            <el-empty :image-size="60" description="配置集合和指标后显示预览" />
          </div>
          <template v-else>
            <MetricCard
              v-if="form.type === 'metric'"
              :value="getSingleValue(previewData)"
              :subtitle="metricSubtitle"
            />
            <DataTableWidget
              v-else-if="form.type === 'dataTable'"
              :result="previewData"
              :group-label="groupPreviewLabel"
              :column-label="breakdownPreviewLabel"
              :metric-labels="previewMetricLabels"
              :value-label="metricSubtitle"
            />
            <ChartRenderer
              v-else
              :type="form.type"
              :result="previewData"
              :metric-key="previewMetricKey"
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
import { computed, reactive, ref, watch } from 'vue'
import {
  Refresh,
  Loading,
  Close,
  Odometer,
  Histogram,
  TrendCharts,
  PieChart as PieChartIcon,
  DataLine,
  Grid,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  aggregate,
  getMetricName,
  METRIC_TYPE_OPTIONS,
  WIDGET_TYPE_OPTIONS,
} from '@/api/dashboard'
import type {
  AggregateResult,
  DashboardWidget,
  DateHistogramInterval,
  GroupByDef,
  GroupByType,
  MetricDef,
  MetricType,
  SortType,
  WidgetType,
} from '@/api/dashboard'
import { usePageConfigStore } from '@/stores/pageConfig'
import { v4 as uuidv4 } from 'uuid'
import ChartRenderer from './ChartRenderer.vue'
import DataTableWidget from './DataTableWidget.vue'
import MetricCard from './MetricCard.vue'

const iconMap: Record<string, any> = {
  Odometer,
  Histogram,
  TrendCharts,
  PieChart: PieChartIcon,
  DataLine,
  Grid,
}

interface RangeFormItem {
  key: string
  from: string
  to: string
}

interface FilterFormItem {
  field: string
  value: string
}

interface EditorFormState {
  type: WidgetType
  collection: string
  metricType: MetricType
  metricField: string
  enableGroup: boolean
  groupField: string
  groupType: GroupByType
  groupDateInterval: DateHistogramInterval
  groupHistogramInterval: number
  groupRanges: RangeFormItem[]
  enableBreakdown: boolean
  breakdownField: string
  breakdownType: GroupByType
  breakdownDateInterval: DateHistogramInterval
  breakdownHistogramInterval: number
  breakdownRanges: RangeFormItem[]
  sort: SortType
  limit: number
  filters: FilterFormItem[]
  title: string
  w: number
  h: number
  gaugeTarget: number
  funnelShowRate: boolean
}

interface Props {
  modelValue: boolean
  widget?: DashboardWidget | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'confirm', widget: DashboardWidget): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const isEdit = computed(() => !!props.widget?.id)
const pageConfigStore = usePageConfigStore()

function createRangeItem(): RangeFormItem {
  return { key: '', from: '', to: '' }
}

function createDefaultForm(): EditorFormState {
  return {
    type: 'bar',
    collection: '',
    metricType: 'count',
    metricField: '',
    enableGroup: true,
    groupField: '',
    groupType: 'terms',
    groupDateInterval: 'month',
    groupHistogramInterval: 10,
    groupRanges: [createRangeItem()],
    enableBreakdown: false,
    breakdownField: '',
    breakdownType: 'terms',
    breakdownDateInterval: 'month',
    breakdownHistogramInterval: 10,
    breakdownRanges: [createRangeItem()],
    sort: 'value_desc',
    limit: 10,
    filters: [],
    title: '',
    w: 6,
    h: 2,
    gaugeTarget: 100,
    funnelShowRate: false,
  }
}

const form = reactive<EditorFormState>(createDefaultForm())
const previewData = ref<AggregateResult | null>(null)
const previewLoading = ref(false)

const collections = computed(() => pageConfigStore.pageConfigs.map(config => ({
  label: config.name,
  value: config.id.replace('page-', ''),
})))

const currentFields = computed(() => {
  const pageId = `page-${form.collection}`
  return pageConfigStore.pageConfigs.find(item => item.id === pageId)?.fields || []
})

const allFields = computed(() => currentFields.value
  .filter(field => !field.hidden)
  .map(field => ({ label: field.label, value: field.fieldName, controlType: field.controlType })))

const numericFields = computed(() => allFields.value
  .filter(field => ['number', 'autoSequence'].includes(field.controlType)))

const arrayFields = computed(() => allFields.value
  .filter(field => ['relation', 'quoteSelect', 'multiSelect', 'checkbox'].includes(field.controlType)))

const relationFields = computed(() => allFields.value
  .filter(field => field.controlType === 'relation'))

const metricFields = computed(() => {
  if (form.metricType.startsWith('relationCount')) return relationFields.value
  if (form.metricType.startsWith('arrayLength')) return arrayFields.value
  return numericFields.value
})

const dateFields = computed(() => allFields.value
  .filter(field => ['date', 'datetime', 'autoTimestamp'].includes(field.controlType)))

const needMetricField = computed(() =>
  METRIC_TYPE_OPTIONS.find(item => item.value === form.metricType)?.needField ?? false,
)

const supportsGrouping = computed(() => form.type !== 'metric')
const supportsBreakdown = computed(() => ['bar', 'line', 'area', 'dataTable'].includes(form.type))
const metricSubtitle = computed(() => METRIC_TYPE_OPTIONS.find(item => item.value === form.metricType)?.label || '')
const previewMetricKey = computed(() => getMetricName({
  type: form.metricType,
  field: form.metricField || undefined,
}))
const previewMetricLabels = computed(() => ({
  [previewMetricKey.value || 'value']: metricSubtitle.value,
}))
const groupPreviewLabel = computed(() => lookupFieldLabel(form.collection, form.groupField, '分组'))
const breakdownPreviewLabel = computed(() => lookupFieldLabel(form.collection, form.breakdownField, '系列'))
const groupFieldOptions = computed(() => getFieldOptions(form.groupType))
const breakdownFieldOptions = computed(() => getFieldOptions(form.breakdownType))

function lookupFieldLabel(collection: string, fieldName: string, fallback: string): string {
  if (!fieldName) return fallback
  const pageId = `page-${collection}`
  const page = pageConfigStore.pageConfigs.find(item => item.id === pageId)
  const field = page?.fields?.find((item: any) => item.fieldName === fieldName)
  return field?.label || fieldName
}

function getFieldOptions(type: GroupByType) {
  if (type === 'histogram' || type === 'range') return numericFields.value
  if (type === 'dateHistogram' && dateFields.value.length) return dateFields.value
  return allFields.value
}

function onCollectionChange() {
  form.groupField = ''
  form.breakdownField = ''
  form.metricField = ''
  previewData.value = null
}

function addRange(target: 'group' | 'breakdown') {
  if (target === 'group') form.groupRanges.push(createRangeItem())
  else form.breakdownRanges.push(createRangeItem())
}

function removeRange(target: 'group' | 'breakdown', index: number) {
  const list = target === 'group' ? form.groupRanges : form.breakdownRanges
  list.splice(index, 1)
  if (!list.length) list.push(createRangeItem())
}

function parseOptionalNumber(value: string): number | undefined {
  const text = String(value ?? '').trim()
  if (!text) return undefined
  const parsed = Number(text)
  return Number.isFinite(parsed) ? parsed : undefined
}

function normalizeRanges(items: RangeFormItem[]) {
  return items
    .map(item => ({
      key: item.key.trim() || undefined,
      from: parseOptionalNumber(item.from),
      to: parseOptionalNumber(item.to),
    }))
    .filter(item => item.key || item.from !== undefined || item.to !== undefined)
}

function buildGroupConfig(
  field: string,
  type: GroupByType,
  dateInterval: DateHistogramInterval,
  histogramInterval: number,
  ranges: RangeFormItem[],
): GroupByDef | undefined {
  if (!field) return undefined

  const config: GroupByDef = {
    field,
    type,
  }

  if (type === 'dateHistogram') {
    config.interval = dateInterval
  } else if (type === 'histogram') {
    config.interval = histogramInterval || 1
  } else if (type === 'range') {
    config.ranges = normalizeRanges(ranges)
  }

  return config
}

function buildFilterPayload() {
  const filter: Record<string, any> = {}
  for (const item of form.filters) {
    if (item.field && item.value) filter[item.field] = item.value
  }
  return Object.keys(filter).length ? filter : undefined
}

function buildMetricConfig(): MetricDef[] {
  return [{
    type: form.metricType,
    field: form.metricField || undefined,
  }]
}

function buildRequestPayload() {
  const groupBy = supportsGrouping.value && form.enableGroup
    ? buildGroupConfig(
        form.groupField,
        form.groupType,
        form.groupDateInterval,
        form.groupHistogramInterval,
        form.groupRanges,
      )
    : undefined

  const breakdownBy = groupBy && supportsBreakdown.value && form.enableBreakdown
    ? buildGroupConfig(
        form.breakdownField,
        form.breakdownType,
        form.breakdownDateInterval,
        form.breakdownHistogramInterval,
        form.breakdownRanges,
      )
    : undefined

  return {
    collection: form.collection,
    metrics: buildMetricConfig(),
    groupBy,
    breakdownBy,
    filter: buildFilterPayload(),
    sort: form.sort,
    limit: form.limit,
    gaugeTarget: form.type === 'gauge' ? form.gaugeTarget : undefined,
    funnelShowRate: form.type === 'funnel' ? form.funnelShowRate : undefined,
  }
}

async function loadPreview() {
  if (!form.collection) return
  previewLoading.value = true
  try {
    previewData.value = await aggregate(buildRequestPayload())
  } catch {
    previewData.value = null
  } finally {
    previewLoading.value = false
  }
}

function getSingleValue(result?: AggregateResult | null) {
  return result?.type === 'single' ? result.value ?? 0 : 0
}

function fillFormFromWidget(widget?: DashboardWidget | null) {
  Object.assign(form, createDefaultForm())
  if (!widget) return

  const metric = widget.config.metrics?.[0]
  form.type = widget.type
  form.collection = widget.config.collection
  form.metricType = metric?.type || 'count'
  form.metricField = metric?.field || ''
  form.enableGroup = !!widget.config.groupBy && widget.type !== 'metric'
  form.groupField = widget.config.groupBy?.field || ''
  form.groupType = widget.config.groupBy?.type || 'terms'
  form.groupDateInterval = (widget.config.groupBy?.interval as DateHistogramInterval) || 'month'
  form.groupHistogramInterval = Number(widget.config.groupBy?.interval) || 10
  form.groupRanges = widget.config.groupBy?.ranges?.length
    ? widget.config.groupBy.ranges.map(item => ({
        key: item.key || '',
        from: item.from !== undefined ? String(item.from) : '',
        to: item.to !== undefined ? String(item.to) : '',
      }))
    : [createRangeItem()]
  form.enableBreakdown = !!widget.config.breakdownBy
  form.breakdownField = widget.config.breakdownBy?.field || ''
  form.breakdownType = widget.config.breakdownBy?.type || 'terms'
  form.breakdownDateInterval = (widget.config.breakdownBy?.interval as DateHistogramInterval) || 'month'
  form.breakdownHistogramInterval = Number(widget.config.breakdownBy?.interval) || 10
  form.breakdownRanges = widget.config.breakdownBy?.ranges?.length
    ? widget.config.breakdownBy.ranges.map(item => ({
        key: item.key || '',
        from: item.from !== undefined ? String(item.from) : '',
        to: item.to !== undefined ? String(item.to) : '',
      }))
    : [createRangeItem()]
  form.sort = widget.config.sort || 'value_desc'
  form.limit = widget.config.limit || 10
  form.filters = widget.config.filter
    ? Object.entries(widget.config.filter).map(([field, value]) => ({ field, value: String(value) }))
    : []
  form.title = widget.title
  form.w = widget.w
  form.h = widget.h
  form.gaugeTarget = widget.config.gaugeTarget || 100
  form.funnelShowRate = widget.config.funnelShowRate || false
}

function handleConfirm() {
  if (!form.collection || !form.title.trim()) {
    ElMessage.warning('请先选择数据集合并填写图表标题')
    return
  }
  if (needMetricField.value && !form.metricField) {
    ElMessage.warning('当前指标需要选择字段')
    return
  }

  const widget: DashboardWidget = {
    id: props.widget?.id || `w-${uuidv4().slice(0, 8)}`,
    type: form.type,
    title: form.title.trim(),
    x: props.widget?.x ?? 0,
    y: props.widget?.y ?? 0,
    w: form.w,
    h: form.h,
    config: buildRequestPayload(),
  }

  emit('confirm', widget)
  visible.value = false
}

let previewTimer: ReturnType<typeof setTimeout> | null = null

watch(() => form.type, (type) => {
  if (type === 'metric') {
    form.enableGroup = false
    form.enableBreakdown = false
  }
  if (!supportsBreakdown.value) {
    form.enableBreakdown = false
  }
})

watch(() => form.groupType, () => {
  form.groupField = ''
  if (form.groupType === 'range' && !form.groupRanges.length) {
    form.groupRanges.push(createRangeItem())
  }
})

watch(() => form.breakdownType, () => {
  form.breakdownField = ''
  if (form.breakdownType === 'range' && !form.breakdownRanges.length) {
    form.breakdownRanges.push(createRangeItem())
  }
})

watch(form, () => {
  if (!visible.value) return
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(() => {
    void loadPreview()
  }, 400)
}, { deep: true })

watch(visible, async (value) => {
  if (!value) return
  if (!pageConfigStore.pageConfigs.length) {
    await pageConfigStore.fetchPageConfigs()
  }
  fillFormFromWidget(props.widget)
  previewData.value = null
})
</script>

<style scoped lang="scss">
.editor-body {
  display: flex;
  gap: 20px;
  min-height: 560px;
}

.editor-config {
  width: 400px;
  flex-shrink: 0;
  overflow-y: auto;
  max-height: 68vh;
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
  min-height: 320px;
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
  background: #fff;
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

.filter-row,
.range-row {
  align-items: center;
}

.filter-eq {
  color: #909399;
  font-size: 13px;
}

.range-list {
  margin-bottom: 8px;
}

.is-loading {
  animation: spin 1.5s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
