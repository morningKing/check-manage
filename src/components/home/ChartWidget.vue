<!--
 * 图表区块 Widget
 *
 * 选一个数据页 + 一个分组字段，按取值分组计数，用 echarts 渲染柱状/饼图/折线。
 * 复用仪表盘的 /dashboards/aggregate 聚合接口与 ChartRenderer 渲染组件。
 -->
<template>
  <el-card v-loading="loading" class="chart-widget">
    <template #header>
      <span class="chart-title">{{ title || '图表' }}</span>
    </template>
    <ChartRenderer
      v-if="result && !empty"
      :type="content.chartType"
      :result="result"
      metric-key="count"
      class="chart-body"
    />
    <el-empty v-else :description="emptyHint" :image-size="60" />
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import ChartRenderer from '@/components/dashboard/ChartRenderer.vue'
import { aggregate } from '@/api/dashboard'
import type { AggregateResult } from '@/api/dashboard'
import type { WidgetContentMap } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['chart']
  title?: string
}>()

const loading = ref(false)
const result = ref<AggregateResult | null>(null)

const empty = computed(() => {
  const r = result.value
  return !r || (r.type === 'grouped' && (!r.data || r.data.length === 0))
})

const emptyHint = computed(() =>
  (!props.content.collection || !props.content.groupField) ? '请先配置数据页与分组字段' : '暂无数据')

async function fetchChart(): Promise<void> {
  if (!props.content.collection || !props.content.groupField) {
    result.value = null
    return
  }
  loading.value = true
  try {
    result.value = await aggregate({
      collection: props.content.collection,
      metrics: [{ type: 'count' }],
      metric: 'count',
      groupBy: { field: props.content.groupField, type: 'terms' },
      groupField: props.content.groupField,
      sort: 'value_desc',
      limit: props.content.limit || 20,
    })
  } catch (error) {
    console.error('图表区块数据加载失败:', error)
    result.value = null
  } finally {
    loading.value = false
  }
}

onMounted(fetchChart)
watch(() => [props.content.collection, props.content.groupField, props.content.chartType, props.content.limit], fetchChart)
</script>

<style scoped lang="scss">
.chart-title { font-weight: 600; }
.chart-body { width: 100%; height: 280px; }
</style>
