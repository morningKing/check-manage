# 日历视图功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为配置驱动的动态数据管理平台新增日历视图，作为第四种数据展示模式。

**Architecture:** 采用 FullCalendar 作为日历渲染引擎，遵循现有视图扩展模式（类型定义 → 组件实现 → index.ts 导出 → DynamicPage.vue 集成）。

**Tech Stack:** Vue 3 + TypeScript + FullCalendar + Element Plus

---

## 文件结构

| 文件 | 负责内容 |
|------|----------|
| `src/types/page.ts` | CalendarConfig 类型定义，ViewConfig 扩展 |
| `src/components/common/CalendarView.vue` | 日历视图主组件，封装 FullCalendar |
| `src/components/common/composables/useCalendarConfig.ts` | 日历配置解析、颜色映射、数据转换 |
| `src/components/common/index.ts` | 导出 CalendarView |
| `src/views/dynamic/DynamicPage.vue` | 集成日历视图，处理事件 |
| `package.json` | 新增 FullCalendar 依赖 |

---

## Task 1: 安装 FullCalendar 依赖

**Files:**
- Modify: `package.json`

- [ ] **Step 1: 添加 FullCalendar 依赖**

```bash
npm install @fullcalendar/vue3 @fullcalendar/core @fullcalendar/daygrid @fullcalendar/timegrid @fullcalendar/interaction
```

- [ ] **Step 2: 验证安装成功**

Run: `npm ls @fullcalendar/vue3`
Expected: 显示版本号（如 6.1.x）

- [ ] **Step 3: Commit**

```bash
git add package.json package-lock.json
git commit -m "chore: add FullCalendar dependencies for calendar view"
```

---

## Task 2: 扩展类型定义

**Files:**
- Modify: `src/types/page.ts`

- [ ] **Step 1: 在 ViewConfig 接口上方添加 CalendarConfig 接口**

找到 `src/types/page.ts` 中 `export interface KanbanConfig` 的位置（约第 59 行），在其后添加：

```typescript
export interface CalendarConfig {
  /** 日期字段名（必填，必须是 date 或 datetime 类型） */
  dateField: string
  /** 结束日期字段名（可选，支持跨天事件） */
  endDateField?: string
  /** 卡片标题字段 */
  cardTitle: string
  /** 颜色标记字段（可选，按状态/优先级着色） */
  cardColorField?: string
  /** 默认视图模式 */
  defaultMode?: 'month' | 'week'
}
```

- [ ] **Step 2: 扩展 ViewConfig 接口**

找到 `export interface ViewConfig`（约第 76 行），修改为：

```typescript
export interface ViewConfig {
  defaultView?: 'table' | 'kanban' | 'excel' | 'calendar'
  kanban?: KanbanConfig
  excel?: ExcelViewConfig
  calendar?: CalendarConfig
}
```

- [ ] **Step 3: 验证类型编译**

Run: `npx vue-tsc --noEmit`
Expected: 无错误输出

- [ ] **Step 4: Commit**

```bash
git add src/types/page.ts
git commit -m "feat(types): add CalendarConfig interface and extend ViewConfig"
```

---

## Task 3: 创建 useCalendarConfig composable

**Files:**
- Create: `src/components/common/composables/useCalendarConfig.ts`

- [ ] **Step 1: 创建 composable 文件**

```typescript
// src/components/common/composables/useCalendarConfig.ts
import type { CalendarConfig, FieldConfig, DynamicRecord } from '@/types'
import type { EventInput } from '@fullcalendar/core'

/**
 * 状态颜色映射表
 * 复用 KanbanBoard 的颜色逻辑
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

/**
 * 获取颜色值
 */
export function getColorForValue(value: any): string {
  if (value == null) return '#409EFF'
  return colorMap[String(value).toLowerCase()] || '#409EFF'
}

/**
 * FullCalendar 的 end 日期是排他性的
 * 需要加一天才能正确显示跨天事件
 */
function addDay(dateStr: string): string {
  const d = new Date(dateStr)
  d.setDate(d.getDate() + 1)
  return d.toISOString().split('T')[0]
}

/**
 * 格式化日期为 YYYY-MM-DD 格式
 */
export function formatDateToISO(date: Date): string {
  return date.toISOString().split('T')[0]
}

/**
 * 将 DynamicRecord 转换为 FullCalendar Event Object
 */
export function transformToEvents(
  records: DynamicRecord[],
  config: CalendarConfig,
  fields: FieldConfig[]
): EventInput[] {
  return records
    .filter(r => r[config.dateField])  // 必须有日期
    .map(r => {
      const colorValue = config.cardColorField ? r[config.cardColorField] : null
      const bgColor = getColorForValue(colorValue)
      
      const endValue = config.endDateField ? r[config.endDateField] : null
      const endDate = endValue ? addDay(String(endValue)) : undefined
      
      return {
        id: r.id,
        title: r[config.cardTitle] || '无标题',
        start: r[config.dateField],
        end: endDate,
        backgroundColor: bgColor,
        borderColor: bgColor,
        textColor: '#303133',
        extendedProps: { 
          record: r,
          colorField: config.cardColorField,
          colorValue: colorValue
        }
      }
    })
}

/**
 * 验证日历配置是否有效
 */
export function validateCalendarConfig(
  config: CalendarConfig | undefined,
  fields: FieldConfig[]
): { valid: boolean; reason?: string } {
  if (!config) {
    return { valid: false, reason: '缺少日历配置' }
  }
  
  if (!config.dateField) {
    return { valid: false, reason: '缺少日期字段配置' }
  }
  
  const dateField = fields.find(f => f.fieldName === config.dateField)
  if (!dateField) {
    return { valid: false, reason: `日期字段 ${config.dateField} 不存在` }
  }
  
  if (!['date', 'datetime'].includes(dateField.controlType)) {
    return { valid: false, reason: `字段 ${config.dateField} 不是日期类型` }
  }
  
  if (!config.cardTitle) {
    return { valid: false, reason: '缺少标题字段配置' }
  }
  
  return { valid: true }
}

/**
 * Composable: 日历配置处理
 */
export function useCalendarConfig(
  config: CalendarConfig | undefined,
  fields: FieldConfig[],
  records: DynamicRecord[]
) {
  const isValid = validateCalendarConfig(config, fields)
  
  const events = isValid.valid 
    ? transformToEvents(records, config!, fields)
    : []
  
  const canResize = config?.endDateField ? true : false
  
  return {
    isValid,
    events,
    canResize,
    getColorForValue,
    formatDateToISO
  }
}
```

- [ ] **Step 2: 验证类型编译**

Run: `npx vue-tsc --noEmit`
Expected: 无错误输出

- [ ] **Step 3: Commit**

```bash
git add src/components/common/composables/useCalendarConfig.ts
git commit -m "feat: add useCalendarConfig composable for calendar data transformation"
```

---

## Task 4: 创建 CalendarView.vue 组件

**Files:**
- Create: `src/components/common/CalendarView.vue`

- [ ] **Step 1: 创建 CalendarView.vue 组件**

```vue
<template>
  <div class="calendar-view">
    <FullCalendar
      ref="calendarRef"
      :options="calendarOptions"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import FullCalendar from '@fullcalendar/vue3'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import type { CalendarConfig, FieldConfig, DynamicRecord } from '@/types'
import type { EventClickArg, DateClickArg, EventDropArg, EventResizeArg, CalendarApi } from '@fullcalendar/core'
import { useCalendarConfig, formatDateToISO, getColorForValue } from './composables/useCalendarConfig'

interface Props {
  /** 数据列表 */
  data: DynamicRecord[]
  /** 字段配置列表 */
  fields: FieldConfig[]
  /** 日历视图配置 */
  config: CalendarConfig
}

const props = defineProps<Props>()

const emit = defineEmits<{
  /** 点击卡片 */
  'card-click': [record: DynamicRecord]
  /** 日期变更（拖拽/拉伸） */
  'date-change': [payload: { recordId: string; updates: Record<string, any> }]
  /** 点击空白日期 */
  'date-click': [date: Date]
}>()

const calendarRef = ref<{ getApi: () => CalendarApi } | null>(null)

// 颜色映射（扩展版）
const extendedColorMap: Record<string, string> = {
  pending: '#E6A23C',   '待处理': '#E6A23C',
  progress: '#409EFF',  '进行中': '#409EFF', '执行中': '#409EFF',
  done: '#67C23A',      '已完成': '#67C23A', '完成': '#67C23A',
  blocked: '#F56C6C',   '阻塞': '#F56C6C',   '暂停': '#F56C6C',
  high: '#F56C6C',      '高': '#F56C6C',     '紧急': '#F56C6C',
  medium: '#E6A23C',    '中': '#E6A23C',     '一般': '#E6A23C',
  low: '#67C23A',       '低': '#67C23A',
}

// 使用 composable 处理数据
const { events, canResize } = useCalendarConfig(
  props.config,
  props.fields,
  props.data
)

// FullCalendar 配置
const calendarOptions = computed(() => ({
  plugins: [dayGridPlugin, timeGridPlugin, interactionPlugin],
  initialView: props.config.defaultMode === 'week' ? 'timeGridWeek' : 'dayGridMonth',
  locale: 'zh-cn',
  headerToolbar: {
    left: 'prev,next today',
    center: 'title',
    right: 'dayGridMonth,timeGridWeek'
  },
  buttonText: {
    today: '今天',
    month: '月',
    week: '周'
  },
  events: events,
  editable: true,
  selectable: true,
  selectMirror: true,
  dayMaxEvents: true,
  weekends: true,
  // 点击事件
  eventClick: handleEventClick,
  // 点击空白日期
  dateClick: handleDateClick,
  // 拖拽移动
  eventDrop: handleEventDrop,
  // 边缘拉伸（仅当有 endDateField 时启用）
  eventResize: canResize ? handleEventResize : undefined,
  // 自定义事件渲染
  eventDidMount: handleEventDidMount,
  // 高度设置
  height: 'auto',
  contentHeight: 600,
}))

// 点击卡片
function handleEventClick(arg: EventClickArg) {
  const record = arg.event.extendedProps.record as DynamicRecord
  if (record) {
    emit('card-click', record)
  }
}

// 点击空白日期
function handleDateClick(arg: DateClickArg) {
  emit('date-click', arg.date)
}

// 拖拽移动
function handleEventDrop(arg: EventDropArg) {
  const record = arg.event.extendedProps.record as DynamicRecord
  if (!record) return
  
  const deltaDays = arg.delta.days
  const updates: Record<string, any> = {}
  
  // 更新开始日期
  const oldStart = new Date(record[props.config.dateField])
  const newStart = new Date(oldStart)
  newStart.setDate(newStart.getDate() + deltaDays)
  updates[props.config.dateField] = formatDateToISO(newStart)
  
  // 如果有结束日期，同步更新
  if (props.config.endDateField && record[props.config.endDateField]) {
    const oldEnd = new Date(record[props.config.endDateField])
    const newEnd = new Date(oldEnd)
    newEnd.setDate(newEnd.getDate() + deltaDays)
    updates[props.config.endDateField] = formatDateToISO(newEnd)
  }
  
  emit('date-change', { recordId: record.id, updates })
}

// 边缘拉伸
function handleEventResize(arg: EventResizeArg) {
  const record = arg.event.extendedProps.record as DynamicRecord
  if (!record || !props.config.endDateField) return
  
  const updates: Record<string, any> = {}
  
  // 获取新的结束日期（FullCalendar end 是排他性的，需要减一天）
  const newEnd = arg.event.end
  if (newEnd) {
    const adjustedEnd = new Date(newEnd)
    adjustedEnd.setDate(adjustedEnd.getDate() - 1)
    updates[props.config.endDateField] = formatDateToISO(adjustedEnd)
  }
  
  emit('date-change', { recordId: record.id, updates })
}

// 自定义事件卡片样式
function handleEventDidMount(info: { el: HTMLElement; event: any }) {
  const el = info.el
  const colorValue = info.event.extendedProps.colorValue
  
  // 添加颜色条样式
  const bgColor = getColorForValue(colorValue)
  el.style.borderLeft = `4px solid ${bgColor}`
  el.style.borderRadius = '4px'
  el.style.padding = '2px 6px'
  el.style.fontSize = '13px'
  el.style.cursor = 'pointer'
  el.style.background = '#fff'
  el.style.boxShadow = '0 1px 2px rgba(0,0,0,0.1)'
}

// 监听数据变化，重新渲染
watch(() => props.data, () => {
  const api = calendarRef.value?.getApi()
  if (api) {
    api.removeAllEvents()
    const { events: newEvents } = useCalendarConfig(
      props.config,
      props.fields,
      props.data
    )
    api.addEventSource(newEvents)
  }
}, { deep: true })

// 切换视图模式
function changeView(mode: 'month' | 'week') {
  const api = calendarRef.value?.getApi()
  if (api) {
    api.changeView(mode === 'week' ? 'timeGridWeek' : 'dayGridMonth')
  }
}

// 暴露方法供父组件调用
defineExpose({
  changeView,
  getApi: () => calendarRef.value?.getApi()
})
</script>

<style scoped>
.calendar-view {
  height: 100%;
  overflow: auto;
}

/* FullCalendar 样式覆盖 */
.calendar-view :deep(.fc) {
  font-family: inherit;
}

.calendar-view :deep(.fc-toolbar-title) {
  font-size: 18px;
  font-weight: 600;
}

.calendar-view :deep(.fc-button) {
  background: #f5f7fa;
  border: 1px solid #dcdfe6;
  color: #606266;
  padding: 6px 12px;
  font-size: 13px;
}

.calendar-view :deep(.fc-button:hover) {
  background: #ecf5ff;
}

.calendar-view :deep(.fc-button-primary) {
  background-color: var(--el-color-primary);
  border-color: var(--el-color-primary);
  color: #fff;
}

.calendar-view :deep(.fc-button-active) {
  background: var(--el-color-primary);
  color: #fff;
}

.calendar-view :deep(.fc-daygrid-day) {
  border: 1px solid #e4e7ed;
}

.calendar-view :deep(.fc-daygrid-day-number) {
  color: #303133;
  font-size: 14px;
  font-weight: 500;
}

.calendar-view :deep(.fc-day-today) {
  background-color: #ecf5ff !important;
}

.calendar-view :deep(.fc-event) {
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 13px;
  cursor: pointer;
  margin: 2px 4px;
}

.calendar-view :deep(.fc-event-dragging) {
  opacity: 0.8;
  box-shadow: 0 4px 8px rgba(0,0,0,0.2);
}

.calendar-view :deep(.fc-timegrid-slot) {
  height: 40px;
}
</style>
```

- [ ] **Step 2: 验证组件编译**

Run: `npx vue-tsc --noEmit`
Expected: 无错误输出

- [ ] **Step 3: Commit**

```bash
git add src/components/common/CalendarView.vue
git commit -m "feat: add CalendarView component with FullCalendar integration"
```

---

## Task 5: 导出 CalendarView 组件

**Files:**
- Modify: `src/components/common/index.ts`

- [ ] **Step 1: 在 index.ts 中添加导出**

找到 `src/components/common/index.ts`，在最后一行添加：

```typescript
export { default as CalendarView } from './CalendarView.vue'
```

完整文件内容：

```typescript
/**
 * 通用组件统一导出
 */

export { default as DataTable } from './DataTable.vue'
export { default as ConfirmDialog } from './ConfirmDialog.vue'
export { default as BatchExportDialog } from './BatchExportDialog.vue'
export { default as RelationGraphDialog } from './RelationGraphDialog.vue'
export { default as KanbanBoard } from './KanbanBoard.vue'
export { default as RecordTimeline } from './RecordTimeline.vue'
export { default as NotificationBell } from './NotificationBell.vue'
export { default as WorkflowActions } from './WorkflowActions.vue'
export { default as ProjectVersionManager } from './ProjectVersionManager.vue'
export { default as BeyondCompareMerge } from './BeyondCompareMerge.vue'
export { default as MergeConflictDialog } from './MergeConflictDialog.vue'
export { default as CommandPalette } from './CommandPalette.vue'
export { default as ExcelView } from './ExcelView.vue'
export { default as CalendarView } from './CalendarView.vue'
```

- [ ] **Step 2: 验证导出**

Run: `npx vue-tsc --noEmit`
Expected: 无错误输出

- [ ] **Step 3: Commit**

```bash
git add src/components/common/index.ts
git commit -m "feat: export CalendarView from common components"
```

---

## Task 6: 集成到 DynamicPage.vue

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`

- [ ] **Step 1: 导入 CalendarView 和 Calendar 图标**

找到 import 区域（约第 764-766 行），修改导入：

```typescript
import { Plus, Refresh, Upload, Download, ArrowDown, Search, Delete, DCaret, Grid, Operation, MagicStick, Tickets, Document, Loading, Back, Check, Calendar } from '@element-plus/icons-vue'
import { usePageConfigStore, useMenuStore, useAuthStore, useJumpNavigationStore } from '@/stores'
import { DataTable, ConfirmDialog, RelationGraphDialog, KanbanBoard, RecordTimeline, WorkflowActions, ProjectVersionManager, ExcelView, CalendarView } from '@/components/common'
```

- [ ] **Step 2: 添加日历相关计算属性**

在 `hasKanbanConfig` 计算属性后（约第 1120 行）添加：

```typescript
/**
 * 日历配置
 */
const calendarConfig = computed<CalendarConfig | undefined>(() => {
  return pageConfig.value?.viewConfig?.calendar
})

const hasCalendarConfig = computed(() => {
  if (!calendarConfig.value) return false
  const dateField = pageFields.value.find(f => f.fieldName === calendarConfig.value!.dateField)
  return dateField && ['date', 'datetime'].includes(dateField.controlType)
})
```

需要添加导入类型：

```typescript
import type { PageConfig, FieldConfig, DynamicRecord, ExportScript, KanbanConfig, FieldOption, DeleteBindingConfig, CalendarConfig } from '@/types'
```

- [ ] **Step 3: 扩展 viewMode 类型**

找到 `const viewMode` 定义（约第 1061 行），修改为：

```typescript
const viewMode = ref<'table' | 'kanban' | 'excel' | 'calendar'>('table')
```

- [ ] **Step 4: 在视图切换按钮中添加日历选项**

找到视图切换 radio-group（约第 72-76 行），修改为：

```vue
<el-radio-group v-model="viewMode" size="small" class="view-toggle">
  <el-radio-button value="table"><el-icon><Grid /></el-icon></el-radio-button>
  <el-radio-button value="excel"><el-icon><Document /></el-icon></el-radio-button>
  <el-radio-button v-if="hasKanbanConfig" value="kanban"><el-icon><Operation /></el-icon></el-radio-button>
  <el-radio-button v-if="hasCalendarConfig" value="calendar"><el-icon><Calendar /></el-icon></el-radio-button>
</el-radio-group>
```

- [ ] **Step 5: 在看板视图后添加日历视图组件**

找到看板视图 el-card（约第 286-301 行），在其后添加：

```vue
<!-- 日历视图 -->
<el-card v-show="viewMode === 'calendar'" class="table-card calendar-card">
  <CalendarView
    v-if="calendarConfig"
    :data="filteredData"
    :fields="pageFields"
    :config="calendarConfig"
    @card-click="handleView"
    @date-change="handleCalendarDateChange"
    @date-click="handleCalendarDateClick"
  />
</el-card>
```

- [ ] **Step 6: 添加日历事件处理函数**

在 `handleKanbanCardMove` 函数后添加：

```typescript
/**
 * 处理日历日期变更（拖拽/拉伸）
 */
async function handleCalendarDateChange(payload: { recordId: string; updates: Record<string, any> }): Promise<void> {
  try {
    await pageConfigStore.updatePageData(pageId.value, payload.recordId, payload.updates)
    ElMessage.success('日期已更新')
    await loadPageData()
  } catch (error: any) {
    const resp = error.response?.data
    ElMessage.error(resp?.error || '日期更新失败')
    // 刷新以恢复日历状态
    await loadPageData()
  }
}

/**
 * 处理日历空白日期点击（新增记录）
 */
function handleCalendarDateClick(date: Date): void {
  if (isGuest.value) { ElMessage.warning('访客无操作权限'); return }
  isEditMode.value = false
  const dateStr = date.toISOString().split('T')[0]
  currentRecord.value = {
    [calendarConfig.value!.dateField]: dateStr
  }
  dialogVisible.value = true
}
```

- [ ] **Step 7: 扩展 viewMode watcher**

找到 `watch(viewMode, ...)`（约第 2829 行），在切换逻辑中添加日历视图处理：

```typescript
watch(viewMode, (newMode, oldMode) => {
  // 离开 Excel 视图时，保存快照
  if (oldMode === 'excel' && newMode !== 'excel') {
    excelViewRef.value?.save_snapshot()
  }

  if (newMode === 'excel') {
    // 首次切换到 Excel 视图时初始化组件
    if (!excelInitialized.value) {
      excelInitialized.value = true
    }
    // 显示加载占位，延迟显示实际组件
    excelReady.value = false
    // 加载全量数据
    loadExcelData().then(() => {
      setTimeout(() => {
        excelReady.value = true
      }, 50)
    })
  } else {
    // 切换到其他视图时立即隐藏 Excel 视图
    excelReady.value = false
  }
  
  // 日历视图无需特殊初始化，数据已通过 filteredData 传递
})
```

- [ ] **Step 8: 添加日历卡片样式**

在 `<style scoped>` 区域末尾添加：

```css
.calendar-card {
  flex: 1;
  min-height: 0;
  overflow: hidden;

  :deep(.el-card__body) {
    height: 100%;
    padding: 16px;
    overflow: auto;
    box-sizing: border-box;
  }
}
```

- [ ] **Step 9: 验证集成编译**

Run: `npx vue-tsc --noEmit`
Expected: 无错误输出

- [ ] **Step 10: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat: integrate CalendarView into DynamicPage with drag/drop support"
```

---

## Task 7: 验证功能运行

**Files:**
- 无新增文件

- [ ] **Step 1: 启动开发服务器**

Run: `npm run dev:all`
Expected: 前端启动在 localhost:5173，后端启动在 localhost:3002

- [ ] **Step 2: 手动测试基本功能**

测试步骤：
1. 登录系统（admin / admin123）
2. 找到一个有日期字段的页面（若无，需先在页面配置中添加）
3. 在视图切换区域点击日历图标
4. 验证日历视图显示
5. 点击一个卡片，验证详情对话框打开
6. 切换月/周视图，验证显示正常

- [ ] **Step 3: Commit 验证完成**

```bash
git add -A
git commit -m "test: verify calendar view basic functionality"
```

---

## Task 8: 添加单元测试（可选但推荐）

**Files:**
- Create: `src/components/common/composables/__tests__/useCalendarConfig.test.ts`

- [ ] **Step 1: 创建测试文件**

```typescript
// src/components/common/composables/__tests__/useCalendarConfig.test.ts
import { describe, it, expect } from 'vitest'
import { 
  getColorForValue, 
  formatDateToISO, 
  transformToEvents,
  validateCalendarConfig
} from '../useCalendarConfig'
import type { CalendarConfig, FieldConfig, DynamicRecord } from '@/types'

describe('useCalendarConfig', () => {
  describe('getColorForValue', () => {
    it('returns correct color for known status values', () => {
      expect(getColorForValue('pending')).toBe('#E6A23C')
      expect(getColorForValue('进行中')).toBe('#409EFF')
      expect(getColorForValue('已完成')).toBe('#67C23A')
      expect(getColorForValue('高')).toBe('#F56C6C')
    })

    it('returns default color for unknown values', () => {
      expect(getColorForValue('unknown')).toBe('#409EFF')
      expect(getColorForValue(null)).toBe('#409EFF')
    })
  })

  describe('formatDateToISO', () => {
    it('formats date to YYYY-MM-DD', () => {
      const date = new Date('2026-05-09T12:00:00Z')
      expect(formatDateToISO(date)).toBe('2026-05-09')
    })
  })

  describe('transformToEvents', () => {
    const config: CalendarConfig = {
      dateField: 'startDate',
      endDateField: 'endDate',
      cardTitle: 'title',
      cardColorField: 'status'
    }

    const fields: FieldConfig[] = [
      { id: '1', fieldName: 'startDate', label: '开始日期', controlType: 'date', required: false, order: 1 },
      { id: '2', fieldName: 'endDate', label: '结束日期', controlType: 'date', required: false, order: 2 },
      { id: '3', fieldName: 'title', label: '标题', controlType: 'text', required: false, order: 3 },
      { id: '4', fieldName: 'status', label: '状态', controlType: 'select', required: false, order: 4, options: [
        { label: '进行中', value: 'progress' }
      ]}
    ]

    const records: DynamicRecord[] = [
      { id: '1', title: '任务A', startDate: '2026-05-01', endDate: '2026-05-05', status: 'progress' },
      { id: '2', title: '任务B', startDate: '2026-05-10', status: 'pending' },
      { id: '3', title: '任务C', startDate: null },  // 无日期，应被过滤
    ]

    it('filters records without date', () => {
      const events = transformToEvents(records, config, fields)
      expect(events.length).toBe(2)
    })

    it('generates correct event structure', () => {
      const events = transformToEvents(records, config, fields)
      const event1 = events.find(e => e.id === '1')
      
      expect(event1?.title).toBe('任务A')
      expect(event1?.start).toBe('2026-05-01')
      // end date should be +1 day (FullCalendar exclusive)
      expect(event1?.end).toBe('2026-05-06')
      expect(event1?.backgroundColor).toBe('#409EFF')
    })

    it('handles single-day events without endDateField', () => {
      const singleConfig: CalendarConfig = {
        dateField: 'startDate',
        cardTitle: 'title'
      }
      const events = transformToEvents(records.filter(r => r.id === '2'), singleConfig, fields)
      
      expect(events[0].end).toBeUndefined()
    })
  })

  describe('validateCalendarConfig', () => {
    const fields: FieldConfig[] = [
      { id: '1', fieldName: 'startDate', label: '开始日期', controlType: 'date', required: false, order: 1 },
      { id: '2', fieldName: 'title', label: '标题', controlType: 'text', required: false, order: 3 },
    ]

    it('returns invalid when config is missing', () => {
      const result = validateCalendarConfig(undefined, fields)
      expect(result.valid).toBe(false)
      expect(result.reason).toBe('缺少日历配置')
    })

    it('returns invalid when dateField is missing', () => {
      const config: CalendarConfig = { dateField: '', cardTitle: 'title' }
      const result = validateCalendarConfig(config, fields)
      expect(result.valid).toBe(false)
      expect(result.reason).toBe('缺少日期字段配置')
    })

    it('returns invalid when dateField is not date type', () => {
      const config: CalendarConfig = { dateField: 'title', cardTitle: 'title' }
      const result = validateCalendarConfig(config, fields)
      expect(result.valid).toBe(false)
      expect(result.reason).toContain('不是日期类型')
    })

    it('returns valid for correct config', () => {
      const config: CalendarConfig = { dateField: 'startDate', cardTitle: 'title' }
      const result = validateCalendarConfig(config, fields)
      expect(result.valid).toBe(true)
    })
  })
})
```

- [ ] **Step 2: 运行测试**

Run: `npm run test`
Expected: 测试通过

- [ ] **Step 3: Commit**

```bash
git add src/components/common/composables/__tests__/useCalendarConfig.test.ts
git commit -m "test: add unit tests for useCalendarConfig composable"
```

---

## Task 9: 更新类型导出

**Files:**
- Modify: `src/types/index.ts`

- [ ] **Step 1: 确保 CalendarConfig 已导出**

检查 `src/types/index.ts` 中是否有 `export * from './page'`，若有则 CalendarConfig 已自动导出。若无，添加：

```typescript
export * from './page'
```

- [ ] **Step 2: 验证类型导出**

Run: `npx vue-tsc --noEmit`
Expected: 无错误输出

- [ ] **Step 3: Commit**

```bash
git add src/types/index.ts
git commit -m "chore: ensure CalendarConfig type is exported"
```

---

## 自检清单

| 检查项 | 状态 |
|--------|------|
| Spec 覆盖率 | ✅ 所有 P0 功能均有对应 Task |
| 无占位符 | ✅ 所有步骤包含完整代码 |
| 类型一致性 | ✅ CalendarConfig 定义与使用一致 |
| 文件路径准确 | ✅ 所有路径基于实际项目结构 |

---

## 最终验证

完成所有 Task 后，执行完整验证：

```bash
# 类型检查
npx vue-tsc --noEmit

# 单元测试
npm run test

# 运行开发服务器
npm run dev:all
```

预期结果：
- 类型检查无错误
- 测试全部通过
- 日历视图可正常显示和交互