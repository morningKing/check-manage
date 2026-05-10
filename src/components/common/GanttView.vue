<!-- src/components/common/GanttView.vue -->
<template>
  <div ref="chartRef" class="gantt-view" />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts/core'
import { CustomChart, LinesChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  MarkLineComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { GanttConfig, FieldConfig, DynamicRecord } from '@/types'
import { useGanttConfig } from './composables/useGanttConfig'

echarts.use([
  CustomChart,
  LinesChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  MarkLineComponent,
  CanvasRenderer,
])

const props = defineProps<{
  data: DynamicRecord[]
  fields: FieldConfig[]
  config: GanttConfig
}>()

const emit = defineEmits<{
  'task-click': [record: DynamicRecord]
}>()

const chartRef = ref<HTMLElement | null>(null)
const chart = shallowRef<echarts.ECharts | null>(null)
let resizeObserver: ResizeObserver | null = null

// 使用 composable 处理数据
const { tasks, minDate, maxDate } = useGanttConfig(props.config, props.fields, props.data)

// 今日时间戳
const todayTimestamp = computed(() => {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return today.getTime()
})

// 时间轴格式化函数
function formatTime(timestamp: number): string {
  const d = new Date(timestamp)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// 构建依赖连线数据
const dependencyLines = computed(() => {
  const lines: any[] = []
  const taskIndexMap = new Map<string, number>()
  tasks.value.forEach((t, idx) => taskIndexMap.set(t.id, idx))

  tasks.value.forEach((task, taskIdx) => {
    task.dependencies.forEach(depId => {
      const depIdx = taskIndexMap.get(depId)
      if (depIdx !== undefined) {
        const depTask = tasks.value[depIdx]
        // 连线：从依赖任务的结束点到当前任务的开始点
        lines.push({
          coords: [
            [depTask.endDate, depIdx],
            [task.startDate, taskIdx]
          ],
          lineStyle: {
            color: '#909399',
            width: 1,
            type: 'dashed'
          }
        })
      }
    })
  })
  return lines
})

function buildOption(): echarts.EChartsCoreOption {
  if (tasks.value.length === 0) {
    return {}
  }

  // 扩展时间范围（前后加7天）
  const extendedMin = minDate.value - 7 * 24 * 3600 * 1000
  const extendedMax = maxDate.value + 7 * 24 * 3600 * 1000

  return {
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        if (params.seriesType === 'custom') {
          const task = tasks.value[params.dataIndex]
          const durationDays = Math.ceil((task.endDate - task.startDate) / (24 * 3600 * 1000))
          return `
            <strong>${task.name}</strong><br/>
            开始: ${formatTime(task.startDate)}<br/>
            结束: ${formatTime(task.endDate)}<br/>
            天数: ${durationDays} 天<br/>
            进度: ${task.progress}%
          `
        }
        return ''
      }
    },
    grid: {
      left: 150,
      right: 30,
      top: 30,
      bottom: 50,
      containLabel: false
    },
    xAxis: {
      type: 'time',
      min: extendedMin,
      max: extendedMax,
      axisLabel: {
        formatter: (value: number) => formatTime(value),
        fontSize: 11,
        rotate: 30
      },
      splitLine: {
        lineStyle: { type: 'dashed', color: '#E4E7ED' }
      }
    },
    yAxis: {
      type: 'category',
      data: tasks.value.map(t => t.name),
      axisLabel: {
        fontSize: 12,
        width: 130,
        overflow: 'truncate',
        ellipsis: '...'
      },
      inverse: true,  // 从上到下排列
      splitLine: { show: false }
    },
    dataZoom: [
      {
        type: 'slider',
        xAxisIndex: 0,
        height: 20,
        bottom: 10,
        startValue: extendedMin,
        endValue: extendedMax,
        labelFormatter: (value: number) => formatTime(value)
      },
      {
        type: 'inside',
        xAxisIndex: 0,
        zoomOnMouseWheel: true,
        moveOnMouseMove: true
      }
    ],
    series: [
      // 任务条形（custom series）
      {
        type: 'custom',
        renderItem: (params: any, api: any) => {
          const task = tasks.value[params.dataIndex]
          const categoryIndex = params.dataIndex
          const start = task.startDate
          const end = task.endDate
          const progress = task.progress

          const pointStart = api.coord([start, categoryIndex])
          const pointEnd = api.coord([end, categoryIndex])
          const height = 24

          // 背景（完整任务条）
          const bgRect = {
            type: 'rect',
            shape: {
              x: pointStart[0],
              y: pointStart[1] - height / 2,
              width: pointEnd[0] - pointStart[0],
              height: height
            },
            style: {
              fill: task.color,
              opacity: 0.3
            }
          }

          // 进度条
          const progressWidth = (pointEnd[0] - pointStart[0]) * (progress / 100)
          const progressRect = {
            type: 'rect',
            shape: {
              x: pointStart[0],
              y: pointStart[1] - height / 2,
              width: progressWidth,
              height: height
            },
            style: {
              fill: task.color,
              opacity: 0.8
            }
          }

          return {
            type: 'group',
            children: [bgRect, progressRect]
          }
        },
        data: tasks.value.map(t => [t.startDate, t.endDate]),
        z: 10
      },
      // 依赖连线（lines series）
      {
        type: 'lines',
        coordinateSystem: 'cartesian2d',
        data: dependencyLines.value,
        symbol: ['none', 'arrow'],
        symbolSize: 6,
        z: 5
      }
    ],
    // 今日标记线
    series: [
      {
        type: 'custom',
        renderItem: (params: any, api: any) => {
          const task = tasks.value[params.dataIndex]
          const categoryIndex = params.dataIndex
          const start = task.startDate
          const end = task.endDate
          const progress = task.progress

          const pointStart = api.coord([start, categoryIndex])
          const pointEnd = api.coord([end, categoryIndex])
          const height = 24

          // 背景（完整任务条）
          const bgRect = {
            type: 'rect',
            shape: {
              x: pointStart[0],
              y: pointStart[1] - height / 2,
              width: pointEnd[0] - pointStart[0],
              height: height
            },
            style: {
              fill: task.color,
              opacity: 0.3
            }
          }

          // 进度条
          const progressWidth = (pointEnd[0] - pointStart[0]) * (progress / 100)
          const progressRect = {
            type: 'rect',
            shape: {
              x: pointStart[0],
              y: pointStart[1] - height / 2,
              width: progressWidth,
              height: height
            },
            style: {
              fill: task.color,
              opacity: 0.8
            }
          }

          return {
            type: 'group',
            children: [bgRect, progressRect]
          }
        },
        data: tasks.value.map(t => [t.startDate, t.endDate]),
        z: 10,
        // 今日标记线
        markLine: {
          silent: true,
          symbol: 'none',
          data: [{
            xAxis: todayTimestamp.value,
            lineStyle: {
              color: '#F56C6C',
              width: 2,
              type: 'solid'
            },
            label: {
              formatter: '今日',
              position: 'start'
            }
          }]
        }
      },
      // 依赖连线（lines series）
      {
        type: 'lines',
        coordinateSystem: 'cartesian2d',
        data: dependencyLines.value,
        symbol: ['none', 'arrow'],
        symbolSize: 6,
        z: 5
      }
    ]
  }
}

function render() {
  if (!chart.value || tasks.value.length === 0) {
    chart.value?.clear()
    return
  }
  chart.value.setOption(buildOption(), true)
}

function handleClick(params: any) {
  if (params.seriesType === 'custom') {
    const task = tasks.value[params.dataIndex]
    emit('task-click', task.record)
  }
}

onMounted(() => {
  if (!chartRef.value) return
  chart.value = echarts.init(chartRef.value)
  chart.value.on('click', handleClick)
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

watch(() => [props.data, props.config], render, { deep: true })
</script>

<style scoped>
.gantt-view {
  width: 100%;
  height: 500px;
  min-height: 300px;
}
</style>