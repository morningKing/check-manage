/**
 * ChartRenderer - ECharts 图表渲染组件
 *
 * 根据 widgetType + aggregateData 自动生成 ECharts option 并渲染。
 * 支持：bar / line / pie / area
 */
<template>
  <div ref="chartRef" class="chart-renderer" />
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, shallowRef } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { WidgetType } from '@/api/dashboard'

echarts.use([
  BarChart, LineChart, PieChart,
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
  CanvasRenderer,
])

const props = defineProps<{
  type: WidgetType
  data: Array<{ key: string; value: number }>
  title?: string
}>()

const chartRef = ref<HTMLElement | null>(null)
const chart = shallowRef<echarts.ECharts | null>(null)
let resizeObserver: ResizeObserver | null = null

function buildOption(): echarts.EChartsCoreOption {
  const keys = props.data.map(d => d.key)
  const values = props.data.map(d => d.value)

  const tooltip: any = { trigger: 'axis', confine: true }
  const colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc']

  if (props.type === 'pie') {
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 11 }, itemWidth: 14, itemHeight: 10 },
      color: colors,
      series: [{
        type: 'pie',
        radius: ['30%', '60%'],
        center: ['50%', '43%'],
        data: props.data.map(d => ({ name: d.key, value: d.value })),
        label: { show: true, formatter: '{b}\n{d}%', fontSize: 11 },
        emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.3)' } },
      }],
    }
  }

  // bar / line / area
  const isArea = props.type === 'area'
  const seriesType = props.type === 'bar' ? 'bar' : 'line'

  return {
    tooltip,
    color: colors,
    grid: { left: 8, right: 12, top: 10, bottom: keys.length > 10 ? 48 : 20, containLabel: true },
    xAxis: {
      type: 'category',
      data: keys,
      axisLabel: { fontSize: 11, rotate: keys.length > 8 ? 30 : 0, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      axisLabel: { fontSize: 11 },
      splitLine: { lineStyle: { type: 'dashed' } },
    },
    dataZoom: keys.length > 20 ? [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 4 }] : [],
    series: [{
      type: seriesType,
      data: values,
      barMaxWidth: 40,
      smooth: seriesType === 'line',
      ...(isArea ? { areaStyle: { opacity: 0.25 } } : {}),
      itemStyle: { borderRadius: seriesType === 'bar' ? [3, 3, 0, 0] : undefined },
    }],
  }
}

function render() {
  if (!chart.value || !props.data?.length) {
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

watch(() => [props.type, props.data], render, { deep: true })
</script>

<style scoped>
.chart-renderer {
  width: 100%;
  height: 100%;
  min-height: 80px;
}
</style>
