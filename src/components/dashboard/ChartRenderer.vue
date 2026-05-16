<template>
  <div ref="chartRef" class="chart-renderer" />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart, GaugeChart, RadarChart, FunnelChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  GraphicComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { AggregateResult, WidgetType, DashboardWidget } from '@/api/dashboard'
import {
  toCartesianChartModel,
  toPieChartData,
  toGaugeValue,
  toRadarDataModel,
  toFunnelData,
  getRingTotalValue,
} from './aggregateResult'

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GaugeChart,
  RadarChart,
  FunnelChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  GraphicComponent,
  CanvasRenderer,
])

const props = defineProps<{
  type: WidgetType
  result?: AggregateResult | null
  metricKey?: string
  widget?: DashboardWidget
}>()

const chartRef = ref<HTMLElement | null>(null)
const chart = shallowRef<echarts.ECharts | null>(null)
let resizeObserver: ResizeObserver | null = null

const cartesianModel = computed(() => toCartesianChartModel(props.result, props.metricKey))
const pieData = computed(() => toPieChartData(props.result, props.metricKey))
const gaugeValue = computed(() => toGaugeValue(props.result))
const radarData = computed(() => toRadarDataModel(props.result))
const funnelData = computed(() => toFunnelData(props.result))
const ringTotal = computed(() => getRingTotalValue(props.result, props.metricKey))

function buildOption(): echarts.EChartsCoreOption {
  const colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc']

  // Pie chart
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

  // Ring chart (donut)
  if (props.type === 'ring') {
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
      graphic: [{
        type: 'text',
        left: 'center',
        top: '38%',
        style: {
          text: ringTotal.value.toLocaleString(),
          fontSize: 24,
          fontWeight: 'bold',
          fill: '#303133',
          textAlign: 'center',
        },
      }],
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        center: ['50%', '45%'],
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

  // Gauge chart
  if (props.type === 'gauge') {
    const target = props.widget?.config?.gaugeTarget || 100
    const thresholds = [
      [0.3, '#67e0e3'],
      [0.7, '#37a2da'],
      [1, '#fd666d'],
    ]
    return {
      series: [{
        type: 'gauge',
        center: ['50%', '55%'],
        radius: '75%',
        min: 0,
        max: target,
        splitNumber: 10,
        axisLine: {
          lineStyle: {
            width: 12,
            color: thresholds,
          },
        },
        pointer: {
          width: 4,
          length: '60%',
        },
        axisTick: {
          distance: -12,
          length: 4,
          lineStyle: { color: '#999', width: 1 },
        },
        splitLine: {
          distance: -16,
          length: 8,
          lineStyle: { color: '#999', width: 2 },
        },
        axisLabel: {
          distance: -20,
          fontSize: 11,
          color: '#666',
        },
        detail: {
          valueAnimation: true,
          formatter: `{value}`,
          fontSize: 18,
          offsetCenter: [0, '75%'],
          color: '#303133',
        },
        data: [{ value: gaugeValue.value }],
      }],
    }
  }

  // Radar chart
  if (props.type === 'radar') {
    const dims = radarData.value.dimensions
    const vals = radarData.value.values
    const maxValues = dims.map((_, i) => Math.max(vals[i] * 1.2, 10))
    return {
      tooltip: { trigger: 'item' },
      radar: {
        indicator: dims.map((name, i) => ({
          name,
          max: maxValues[i],
        })),
        radius: '55%',
        center: ['50%', '50%'],
        axisName: { fontSize: 11, color: '#666' },
        splitArea: { areaStyle: { opacity: 0.1 } },
      },
      series: [{
        type: 'radar',
        data: [{
          value: vals,
          name: '数据',
          areaStyle: { opacity: 0.2 },
          lineStyle: { width: 2 },
          symbol: 'circle',
          symbolSize: 6,
        }],
      }],
    }
  }

  // Funnel chart
  if (props.type === 'funnel') {
    const showRate = props.widget?.config?.funnelShowRate
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c}' },
      series: [{
        type: 'funnel',
        left: '10%',
        top: 20,
        bottom: 10,
        width: '80%',
        min: 0,
        max: 100,
        minSize: '0%',
        maxSize: '100%',
        sort: 'descending',
        gap: 2,
        label: {
          show: true,
          position: 'inside',
          formatter: showRate ? '{b}: {c} ({d}%)' : '{b}: {c}',
          fontSize: 12,
        },
        itemStyle: {
          borderWidth: 0,
        },
        emphasis: {
          label: {
            fontSize: 14,
          },
        },
        data: funnelData.value,
      }],
    }
  }

  // Bar, Line, Area charts
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
  if (props.type === 'pie' || props.type === 'ring') return pieData.value.length > 0
  if (props.type === 'gauge') return gaugeValue.value !== 0 || props.result?.type === 'single'
  if (props.type === 'radar') return radarData.value.dimensions.length > 0
  if (props.type === 'funnel') return funnelData.value.length > 0
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
