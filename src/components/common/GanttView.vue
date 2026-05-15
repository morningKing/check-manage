<!-- src/components/common/GanttView.vue -->
<template>
  <div class="gantt-container">
    <!-- 时间轴头部 -->
    <div class="timeline-header" v-if="tasks.length > 0">
      <div class="timeline-label-column">
        <span class="timeline-title">时间轴</span>
      </div>
      <div class="timeline-axis" ref="timelineAxisRef">
        <div
          v-for="marker in timelineMarkers"
          :key="marker.key"
          class="timeline-marker"
          :style="{ left: marker.left + '%' }"
        >
          <span class="marker-label">{{ marker.label }}</span>
        </div>
      </div>
    </div>
    <!-- 甘特图主体 -->
    <div class="gantt-body">
      <div ref="chartRef" class="gantt-view" />
    </div>
  </div>
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
import { transformToGanttTasks, validateGanttConfig, type GanttTask } from './composables/useGanttConfig'

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
const timelineAxisRef = ref<HTMLElement | null>(null)
const chart = shallowRef<echarts.ECharts | null>(null)
let resizeObserver: ResizeObserver | null = null

// 验证配置
const isValid = computed(() => validateGanttConfig(props.config, props.fields))

// 处理任务数据 - 使用 computed 确保响应式
const tasks = computed<GanttTask[]>(() => {
  if (!isValid.value.valid) return []
  return transformToGanttTasks(props.data, props.config!, props.fields)
})

// 计算时间范围
const minDate = computed(() => {
  if (tasks.value.length === 0) return Date.now()
  return Math.min(...tasks.value.map(t => t.startDate))
})

const maxDate = computed(() => {
  if (tasks.value.length === 0) return Date.now() + 7 * 24 * 3600 * 1000
  return Math.max(...tasks.value.map(t => t.endDate))
})

// 扩展时间范围
const extendedMin = computed(() => minDate.value - 7 * 24 * 3600 * 1000)
const extendedMax = computed(() => maxDate.value + 7 * 24 * 3600 * 1000)

// 时间轴标记点（显示月份）
const timelineMarkers = computed(() => {
  if (tasks.value.length === 0) return []

  const markers: { key: string; label: string; left: number }[] = []
  const start = new Date(extendedMin.value)
  const end = new Date(extendedMax.value)
  const totalRange = extendedMax.value - extendedMin.value

  // 从起始月份开始
  let currentMonth = new Date(start.getFullYear(), start.getMonth(), 1)

  while (currentMonth.getTime() <= end.getTime()) {
    const position = ((currentMonth.getTime() - extendedMin.value) / totalRange) * 100
    const monthLabel = `${currentMonth.getFullYear()}/${String(currentMonth.getMonth() + 1).padStart(2, '0')}`

    markers.push({
      key: monthLabel,
      label: monthLabel,
      left: Math.max(0, Math.min(100, position))
    })

    // 下一个月
    currentMonth = new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 1)
  }

  return markers
})

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
  const lines: { coords: [number, number][]; lineStyle: { color: string; width: number; type: string } }[] = []
  const taskIndexMap = new Map<string, number>()
  tasks.value.forEach((t, idx) => taskIndexMap.set(t.id, idx))

  tasks.value.forEach((task, taskIdx) => {
    task.dependencies.forEach(depId => {
      const depIdx = taskIndexMap.get(depId)
      if (depIdx !== undefined) {
        const depTask = tasks.value[depIdx]
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
      top: 20,
      bottom: 50,
      containLabel: false
    },
    xAxis: {
      type: 'time',
      min: extendedMin.value,
      max: extendedMax.value,
      axisLabel: {
        formatter: (value: number) => {
          const d = new Date(value)
          return `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`
        },
        fontSize: 11,
        rotate: 0
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
      inverse: true,
      splitLine: { show: false }
    },
    dataZoom: [
      {
        type: 'slider',
        xAxisIndex: 0,
        height: 20,
        bottom: 10,
        startValue: extendedMin.value,
        endValue: extendedMax.value,
        labelFormatter: (value: number) => {
          const d = new Date(value)
          return `${d.getMonth() + 1}/${d.getDate()}`
        }
      },
      {
        type: 'inside',
        xAxisIndex: 0,
        zoomOnMouseWheel: true,
        moveOnMouseMove: true
      }
    ],
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

// 监听数据变化重新渲染
watch([tasks, minDate, maxDate], render, { deep: true })
</script>

<style scoped>
.gantt-container {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.timeline-header {
  display: flex;
  height: 36px;
  border-bottom: 2px solid var(--el-border-color);
  background: var(--el-fill-color-light);
  flex-shrink: 0;
}

.timeline-label-column {
  width: 150px;
  padding: 8px 12px;
  border-right: 1px solid var(--el-border-color);
  display: flex;
  align-items: center;
}

.timeline-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.timeline-axis {
  flex: 1;
  position: relative;
  overflow: hidden;
}

.timeline-marker {
  position: absolute;
  top: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding-left: 4px;
}

.marker-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-regular);
  background: var(--el-color-primary-light-9);
  padding: 2px 8px;
  border-radius: 4px;
}

.gantt-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.gantt-view {
  width: 100%;
  height: 100%;
  min-height: 300px;
}
</style>