# 甘特图视图实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为动态数据页面添加甘特图视图，支持任务时间条显示、依赖连线、今日标记线。

**Architecture:** 扩展 ViewConfig 类型添加 GanttConfig，创建 GanttView.vue 组件使用 ECharts custom series 渲染甘特图条形和依赖连线，修改 DynamicPage.vue 添加视图切换。

**Tech Stack:** ECharts (custom series, lines series), Vue 3 Composition API, TypeScript

---

## 文件结构

| 文件 | 负责 |
|------|------|
| `src/types/page.ts` | GanttConfig 类型定义，扩展 ViewConfig |
| `src/components/common/GanttView.vue` | 甘特图渲染组件（ECharts custom series） |
| `src/components/common/composables/useGanttConfig.ts` | 甘特图数据处理逻辑 |
| `src/components/common/index.ts` | 导出 GanttView |
| `src/views/dynamic/DynamicPage.vue` | 视图切换按钮 + 甘特图组件渲染 |

---

## Task 1: 定义 GanttConfig 类型

**Files:**
- Modify: `src/types/page.ts:89-94`

- [ ] **Step 1: 在 CalendarConfig 后添加 GanttConfig 类型定义**

```typescript
export interface GanttConfig {
  /** 开始日期字段（必填，必须是 date 或 datetime 类型） */
  startDateField: string
  /** 结束日期字段（必填，必须是 date 或 datetime 类型） */
  endDateField: string
  /** 任务标题字段（必填，用于 Y 轴显示） */
  titleField: string
  /** 进度字段（可选，0-100 数字，显示完成百分比） */
  progressField?: string
  /** 依赖字段（可选，存储依赖任务 ID 数组） */
  dependenciesField?: string
  /** 颜色标记字段（可选，按状态/优先级着色） */
  colorField?: string
}
```

- [ ] **Step 2: 扩展 ViewConfig 类型**

修改 ViewConfig 接口，添加 gantt 配置和 defaultView 类型：

```typescript
export interface ViewConfig {
  defaultView?: 'table' | 'kanban' | 'excel' | 'calendar' | 'gantt'
  kanban?: KanbanConfig
  excel?: ExcelViewConfig
  calendar?: CalendarConfig
  gantt?: GanttConfig
}
```

- [ ] **Step 3: 提交类型定义**

```bash
git add src/types/page.ts
git commit -m "feat: add GanttConfig type definition"
```

---

## Task 2: 创建 useGanttConfig composable

**Files:**
- Create: `src/components/common/composables/useGanttConfig.ts`

- [ ] **Step 1: 创建甘特图数据处理 composable**

```typescript
// src/components/common/composables/useGanttConfig.ts
import type { GanttConfig, FieldConfig, DynamicRecord } from '@/types'

/**
 * 状态颜色映射表（复用 CalendarConfig）
 */
const colorMap: Record<string, string> = {
  pending: '#E6A23C',   '待处理': '#E6A23C',
  progress: '#409EFF',  '进行中': '#409EFF',
  done: '#67C23A',      '已完成': '#67C23A',
  blocked: '#F56C6C',   '阻塞': '#F56C6C',
  high: '#F56C6C',      '高': '#F56C6C',   '紧急': '#F56C6C',
  medium: '#E6A23C',    '中': '#E6A23C',   '一般': '#E6A23C',
  low: '#67C23A',       '低': '#67C23A',
}

export function getColorForValue(value: any): string {
  if (value == null) return '#409EFF'
  return colorMap[String(value).toLowerCase()] || '#409EFF'
}

/**
 * 将日期字符串转换为时间戳
 */
export function dateToTimestamp(dateStr: string | Date | null): number {
  if (!dateStr) return 0
  const d = typeof dateStr === 'string' ? new Date(dateStr) : dateStr
  return d.getTime()
}

/**
 * 甘特图任务数据
 */
export interface GanttTask {
  id: string
  name: string
  startDate: number  // 时间戳
  endDate: number    // 时间戳
  progress: number   // 0-100
  color: string
  dependencies: string[]  // 依赖任务 ID 列表
  record: DynamicRecord   // 原始记录
}

/**
 * 将 DynamicRecord 转换为 GanttTask
 */
export function transformToGanttTasks(
  records: DynamicRecord[],
  config: GanttConfig,
  fields: FieldConfig[]
): GanttTask[] {
  return records
    .filter(r => r[config.startDateField] && r[config.endDateField])
    .map(r => {
      const colorValue = config.colorField ? r[config.colorField] : null
      const progressValue = config.progressField ? Number(r[config.progressField]) || 0 : 0
      const depsValue = config.dependenciesField ? r[config.dependenciesField] : null
      const dependencies = Array.isArray(depsValue)
        ? depsValue.filter(id => records.some(rec => rec.id === id))
        : []

      return {
        id: r.id,
        name: r[config.titleField] || '无标题',
        startDate: dateToTimestamp(r[config.startDateField]),
        endDate: dateToTimestamp(r[config.endDateField]),
        progress: Math.min(100, Math.max(0, progressValue)),
        color: getColorForValue(colorValue),
        dependencies,
        record: r
      }
    })
    .sort((a, b) => a.startDate - b.startDate)
}

/**
 * 验证甘特图配置是否有效
 */
export function validateGanttConfig(
  config: GanttConfig | undefined,
  fields: FieldConfig[]
): { valid: boolean; reason?: string } {
  if (!config) {
    return { valid: false, reason: '缺少甘特图配置' }
  }

  if (!config.startDateField) {
    return { valid: false, reason: '缺少开始日期字段配置' }
  }

  if (!config.endDateField) {
    return { valid: false, reason: '缺少结束日期字段配置' }
  }

  if (!config.titleField) {
    return { valid: false, reason: '缺少标题字段配置' }
  }

  const startField = fields.find(f => f.fieldName === config.startDateField)
  if (!startField || !['date', 'datetime'].includes(startField.controlType)) {
    return { valid: false, reason: `开始日期字段 ${config.startDateField} 不是日期类型` }
  }

  const endField = fields.find(f => f.fieldName === config.endDateField)
  if (!endField || !['date', 'datetime'].includes(endField.controlType)) {
    return { valid: false, reason: `结束日期字段 ${config.endDateField} 不是日期类型` }
  }

  return { valid: true }
}

/**
 * Composable: 甘特图配置处理
 */
export function useGanttConfig(
  config: GanttConfig | undefined,
  fields: FieldConfig[],
  records: DynamicRecord[]
) {
  const isValid = validateGanttConfig(config, fields)

  const tasks = isValid.valid
    ? transformToGanttTasks(records, config!, fields)
    : []

  // 计算时间范围
  const minDate = tasks.length > 0 ? Math.min(...tasks.map(t => t.startDate)) : Date.now()
  const maxDate = tasks.length > 0 ? Math.max(...tasks.map(t => t.endDate)) : Date.now() + 7 * 24 * 3600 * 1000

  return {
    isValid,
    tasks,
    minDate,
    maxDate,
    getColorForValue,
    dateToTimestamp
  }
}
```

- [ ] **Step 2: 提交 composable**

```bash
git add src/components/common/composables/useGanttConfig.ts
git commit -m "feat: add useGanttConfig composable"
```

---

## Task 3: 创建 GanttView.vue 组件

**Files:**
- Create: `src/components/common/GanttView.vue`

- [ ] **Step 1: 创建甘特图视图组件**

```vue
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
import { useGanttConfig, type GanttTask } from './composables/useGanttConfig'

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
  tasks.forEach((t, idx) => taskIndexMap.set(t.id, idx))

  tasks.forEach((task, taskIdx) => {
    task.dependencies.forEach(depId => {
      const depIdx = taskIndexMap.get(depId)
      if (depIdx !== undefined) {
        const depTask = tasks[depIdx]
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
  if (tasks.length === 0) {
    return {}
  }

  // 扩展时间范围（前后加7天）
  const extendedMin = minDate - 7 * 24 * 3600 * 1000
  const extendedMax = maxDate + 7 * 24 * 3600 * 1000

  return {
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        if (params.seriesType === 'custom') {
          const task = tasks[params.dataIndex]
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
      data: tasks.map(t => t.name),
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
          const task = tasks[params.dataIndex]
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
            children: [bgRect, progressRect],
            // 点击事件
            event: {
              click: () => {
                emit('task-click', task.record)
              }
            }
          }
        },
        data: tasks.map(t => [t.startDate, t.endDate]),
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
  }
}

function render() {
  if (!chart.value || tasks.length === 0) {
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

watch(() => [props.data, props.config], render, { deep: true })
</script>

<style scoped>
.gantt-view {
  width: 100%;
  height: 500px;
  min-height: 300px;
}
</style>
```

- [ ] **Step 2: 提交甘特图组件**

```bash
git add src/components/common/GanttView.vue
git commit -m "feat: add GanttView component with ECharts custom series"
```

---

## Task 4: 导出 GanttView 组件

**Files:**
- Modify: `src/components/common/index.ts`

- [ ] **Step 1: 在 index.ts 添加导出**

```typescript
export { default as GanttView } from './GanttView.vue'
```

- [ ] **Step 2: 提交导出**

```bash
git add src/components/common/index.ts
git commit -m "feat: export GanttView from common components"
```

---

## Task 5: 修改 DynamicPage.vue 添加甘特图视图

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`

- [ ] **Step 1: 导入 GanttView 和类型**

在 script setup 导入部分添加：

```typescript
import type { GanttConfig } from '@/types'
import { GanttView } from '@/components/common'
```

- [ ] **Step 2: 添加视图切换按钮**

在第 76 行后添加甘特图按钮：

```vue
<el-radio-button v-if="hasCalendarConfig" value="calendar"><el-icon><Calendar /></el-icon></el-radio-button>
<el-radio-button v-if="hasGanttConfig" value="gantt"><el-icon><DataLine /></el-icon></el-radio-button>
```

需要导入 DataLine 图标：

```typescript
import { DataLine } from '@element-plus/icons-vue'
```

- [ ] **Step 3: 添加甘特图配置 computed**

在第 1146 行后添加：

```typescript
const ganttConfig = computed<GanttConfig | undefined>(() => {
  return pageConfig.value?.viewConfig?.gantt
})

const hasGanttConfig = computed(() => {
  if (!ganttConfig.value) return false
  const startField = pageFields.value.find(f => f.fieldName === ganttConfig.value!.startDateField)
  const endField = pageFields.value.find(f => f.fieldName === ganttConfig.value!.endDateField)
  return startField && endField &&
    ['date', 'datetime'].includes(startField.controlType) &&
    ['date', 'datetime'].includes(endField.controlType)
})
```

- [ ] **Step 4: 添加甘特图组件渲染**

在第 315 行后添加：

```vue
<!-- 甘特图视图 -->
<el-card v-show="viewMode === 'gantt'" class="table-card gantt-card">
  <GanttView
    v-if="ganttConfig"
    :data="filteredData"
    :fields="pageFields"
    :config="ganttConfig"
    @task-click="handleView"
  />
</el-card>
```

- [ ] **Step 5: 提交 DynamicPage.vue 修改**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat: add gantt view toggle and rendering in DynamicPage"
```

---

## Task 6: 测试甘特图功能

**Files:**
- Test: 手动测试

- [ ] **Step 1: 创建测试数据**

在数据库中为某个数据页面配置甘特图视图：

```sql
-- 示例：为 inspection-case 页面配置甘特图
UPDATE page_configs
SET view_config = jsonb_set(
  view_config,
  '{gantt}',
  '{"startDateField": "startDate", "endDateField": "endDate", "titleField": "caseName", "progressField": "progress", "colorField": "status"}'::jsonb
)
WHERE id = 'page-inspection-case';
```

- [ ] **Step 2: 确保数据页面有对应字段**

确保 inspection-case collection 的数据记录有：
- startDate (date 类型)
- endDate (date 类型)
- caseName (text 类型，作为标题)
- progress (number 类型，0-100)
- status (select 类型，用于颜色)

如果没有，需要在 page_configs.fields 中添加这些字段配置。

- [ ] **Step 3: 启动开发服务器测试**

```bash
npm run dev:all
```

访问对应的数据页面，点击视图切换按钮中的甘特图图标，验证：
1. 甘特图正确渲染任务条
2. 时间轴显示正确
3. 今日标记线显示
4. 进度百分比显示
5. 点击任务条打开详情
6. 拖拽时间轴缩放

- [ ] **Step 4: 最终提交**

```bash
git add -A
git commit -m "feat: complete gantt view implementation"
```

---

## 验证清单

- [ ] GanttConfig 类型定义正确
- [ ] useGanttConfig composable 数据转换正确
- [ ] GanttView 组件渲染甘特图条形
- [ ] 今日标记线显示
- [ ] 进度百分比显示（背景 + 进度条）
- [ ] 依赖连线显示（如有 dependenciesField）
- [ ] 时间轴缩放拖拽可用
- [ ] 点击任务打开详情
- [ ] DynamicPage 视图切换按钮显示
- [ ] hasGanttConfig 配置验证正确