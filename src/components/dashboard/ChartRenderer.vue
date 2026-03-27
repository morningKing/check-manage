<template>
  <div ref="chartRef" class="chart-renderer" />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { AggregateResult, WidgetType } from '@/api/dashboard'
import {
  toCartesianChartModel,
  toPieChartData,
} from './aggregateResult'

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  CanvasRenderer,
])

const props = defineProps<{
  type: WidgetType
  result?: AggregateResult | null
  metricKey?: string
}>()

const chartRef = ref<HTMLElement | null>(null)
const chart = shallowRef<echarts.ECharts | null>(null)
let resizeObserver: ResizeObserver | null = null

const cartesianModel = computed(() => toCartesianChartModel(props.result, props.metricKey))
const pieData = computed(() => toPieChartData(props.result, props.metricKey))

function buildOption(): echarts.EChartsCoreOption {
  const colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc']

  if (props.type === 'pie') {
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: {
        type: 'scroll',
        bottom: 0,
        textStyle: { fontSize: 11 },
        itemWidth: 14,
        itemHeight: 10,
      },
      color: colors,
      series: [{
        type: 'pie',
        radius: ['30%', '60%'],
        center: ['50%', '43%'],
        data: pieData.value,
        label: { show: true, formatter: '{b}\n{d}%', fontSize: 11 },
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowOffsetX: 0,
            shadowColor: 'rgba(0,0,0,0.3)',
          },
        },
      }],
    }
  }

  const isArea = props.type === 'area'
  const seriesType = props.type === 'bar' ? 'bar' : 'line'
  const categories = cartesianModel.value.categories

  return {
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: props.type === 'bar' ? 'shadow' : 'line' },
    },
    legend: cartesianModel.value.series.length > 1 ? { top: 0, type: 'scroll' } : undefined,
    color: colors,
    grid: {
      left: 8,
      right: 12,
      top: cartesianModel.value.series.length > 1 ? 36 : 10,
      bottom: categories.length > 10 ? 48 : 20,
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: {
        fontSize: 11,
        rotate: categories.length > 8 ? 30 : 0,
        hideOverlap: true,
      },
    },
    yAxis: {
      type: 'value',
      axisLabel: { fontSize: 11 },
      splitLine: { lineStyle: { type: 'dashed' } },
    },
    dataZoom: categories.length > 20 ? [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 4 }] : [],
    series: cartesianModel.value.series.map(series => ({
      name: series.name,
      type: seriesType,
      data: series.data,
      barMaxWidth: 40,
      smooth: seriesType === 'line',
      ...(isArea ? { areaStyle: { opacity: 0.25 } } : {}),
      itemStyle: { borderRadius: seriesType === 'bar' ? [3, 3, 0, 0] : undefined },
    })),
  }
}

function hasRenderableData() {
  if (!props.result) return false
  if (props.type === 'pie') return pieData.value.length > 0
  return cartesianModel.value.categories.length > 0 && cartesianModel.value.series.length > 0
}

function render() {
  if (!chart.value || !hasRenderableData()) {
    chart.value?.clear()
    return
  }
  chart.value.setOption(buildOption(), true)
}

onMounted(() => {
  if (!chartRef.value) return
  chart.value = echarts.init(chartRef.value)
  render()

  resizeObserver = new ResizeObserver(() => {
    chart.value?.resize()
  })
  resizeObserver.observe(chartRef.value)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  chart.value?.dispose()
})

watch(() => [props.type, props.result, props.metricKey], render, { deep: true })
</script>

<style scoped>
.chart-renderer {
  width: 100%;
  height: 100%;
  min-height: 80px;
}
</style>
