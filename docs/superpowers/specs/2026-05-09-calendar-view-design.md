# 日历视图功能设计文档

## 概述

为配置驱动的动态数据管理平台新增日历视图，作为第四种数据展示模式（现有：表格、看板、Excel）。支持日期型数据的可视化展示，类似 Notion 日历视图，具备拖拽调整、颜色标记、跨天事件等功能。

## 使用场景

1. **项目进度追踪** — 显示任务的开始/结束日期，关注时间线和里程碑
2. **事件安排管理** — 单日事件为主（会议、巡检计划），支持跨天事件

## 技术方案

采用 **FullCalendar** 作为日历渲染引擎。

| 选择理由 | 说明 |
|----------|------|
| 功能完整 | 拖拽移动、边缘拉伸、月/周切换开箱即用 |
| Vue 3 支持 | 官方适配器 `@fullcalendar/vue3` |
| 稳定性高 | 业界标准日历库，经大量用户验证 |
| 开发效率 | 核心复杂交互无需自行实现 |

新增依赖：
- `@fullcalendar/vue3` — Vue 3 适配器
- `@fullcalendar/core` — 核心库
- `@fullcalendar/daygrid` — 月视图
- `@fullcalendar/timegrid` — 周视图
- `@fullcalendar/interaction` — 拖拽交互

---

## 类型定义

### CalendarConfig 接口

```typescript
// src/types/page.ts 新增

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

### ViewConfig 扩展

```typescript
export interface ViewConfig {
  defaultView?: 'table' | 'kanban' | 'excel' | 'calendar'  // 新增 calendar
  kanban?: KanbanConfig
  excel?: ExcelViewConfig
  calendar?: CalendarConfig  // 新增
}
```

### 配置示例

```json
{
  "viewConfig": {
    "defaultView": "calendar",
    "calendar": {
      "dateField": "startDate",
      "endDateField": "endDate",
      "cardTitle": "taskName",
      "cardColorField": "status",
      "defaultMode": "month"
    }
  }
}
```

---

## 组件结构

### 文件新增

```
src/components/common/
├── CalendarView.vue          # 日历视图主组件（新增）
└── index.ts                  # 导出（新增 CalendarView）

src/components/common/composables/
└── useCalendarConfig.ts      # 日历配置解析逻辑（新增）
```

### CalendarView.vue Props

```typescript
interface Props {
  /** 数据列表 */
  data: DynamicRecord[]
  /** 字段配置列表 */
  fields: FieldConfig[]
  /** 日历视图配置 */
  config: CalendarConfig
}

interface Emits {
  /** 点击卡片 */
  'card-click': [record: DynamicRecord]
  /** 日期变更（拖拽/拉伸） */
  'date-change': [payload: { recordId: string; updates: Record<string, any> }]
  /** 点击空白日期 */
  'date-click': [date: Date]
}
```

---

## 数据转换

将 DynamicRecord 转换为 FullCalendar Event Object：

```typescript
function transformToEvents(
  records: DynamicRecord[],
  config: CalendarConfig,
  colorMap: Record<string, string>
): EventInput[] {
  return records
    .filter(r => r[config.dateField])  // 必须有日期
    .map(r => ({
      id: r.id,
      title: r[config.cardTitle] || '无标题',
      start: r[config.dateField],
      end: config.endDateField ? addDay(r[config.endDateField]) : undefined,  // FullCalendar end 是排他日期
      backgroundColor: 'transparent',  // 使用自定义渲染
      borderColor: colorMap[r[config.cardColorField]] || '#409EFF',
      extendedProps: { record: r }
    }))
}

// FullCalendar 的 end 日期是排他性的，需要加一天才能正确显示跨天事件
function addDay(dateStr: string): string {
  const d = new Date(dateStr)
  d.setDate(d.getDate() + 1)
  return d.toISOString().split('T')[0]
}
```

---

## 交互设计

### 事件映射表

| 用户操作 | FullCalendar 事件 | 处理逻辑 |
|----------|-------------------|----------|
| 点击卡片 | `eventClick` | emit `card-click` → 打开查看/编辑对话框 |
| 拖拽卡片 | `eventDrop` | 计算 delta → emit `date-change` |
| 拖拽边缘 | `eventResize` | 计算 new end → emit `date-change` |
| 点击空白日期 | `dateClick` | emit `date-click` → 打开新增对话框 |
| 切换视图 | 内部按钮 | 调用 `calendarApi.changeView()` |

### 拖拽边界处理

| 边界情况 | 处理方式 |
|----------|----------|
| 拖到其他月份 | 允许，视图自动滚动 |
| 跨天事件变单天 | 允许，`endDateField` 设为 null 或等于 `dateField` |
| 单天事件拉伸成跨天 | 允许，自动填充 `endDateField` |
| 无 `endDateField` 配置 | 禁用 `eventResize`（边缘拉伸） |

### DynamicPage.vue 集成

```typescript
// 计算属性
const calendarConfig = computed(() => pageConfig.value?.viewConfig?.calendar)

const hasCalendarConfig = computed(() => {
  if (!calendarConfig.value) return false
  const dateField = pageFields.value.find(f => f.fieldName === calendarConfig.value!.dateField)
  return dateField && ['date', 'datetime'].includes(dateField.controlType)
})

// 事件处理
async function handleCalendarDateChange(payload) {
  await pageConfigStore.updatePageData(pageId.value, payload.recordId, payload.updates)
  await loadPageData()
}

function handleCalendarDateClick(date: Date) {
  isEditMode.value = false
  currentRecord.value = {
    [calendarConfig.value!.dateField]: formatDate(date)
  }
  dialogVisible.value = true
}
```

---

## UI 样式

### 月视图布局

- 7 列网格，显示周日至周六
- 每日单元格内显示日期数字 + 事件卡片列表
- 卡片左侧显示状态颜色条

### 周视图布局

- 左侧显示时间轴（08:00 - 20:00）
- 右侧 7 列显示周一至周日
- 事件卡片按时间位置垂直排列

### 卡片样式

```css
/* 自定义事件卡片 */
.fc-event {
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 13px;
  cursor: pointer;
  background: #fff;
  border-left: 4px solid var(--event-color);
  box-shadow: 0 1px 2px rgba(0,0,0,0.1);
}

/* 拖拽状态 */
.fc-event.fc-event-dragging {
  opacity: 0.8;
  box-shadow: 0 4px 8px rgba(0,0,0,0.2);
}
```

### 状态颜色映射

复用 KanbanBoard 的颜色逻辑：

```typescript
const colorMap: Record<string, string> = {
  pending: '#E6A23C',   '待处理': '#E6A23C',
  progress: '#409EFF',  '进行中': '#409EFF',
  done: '#67C23A',      '已完成': '#67C23A',
  blocked: '#F56C6C',   '阻塞': '#F56C6C',
  high: '#F56C6C',      '高': '#F56C6C',
  medium: '#E6A23C',    '中': '#E6A23C',
  low: '#67C23A',       '低': '#67C23A',
}
```

---

## 配置界面

在 PageConfigManager.vue 的视图配置区域新增：

```
┌─────────────────────────────────────────────────┐
│ ☑ 启用日历视图                                   │
│   日期字段: [开始日期 ▼] ⚠ 必须是日期类型字段     │
│   结束日期: [结束日期 ▼] (可选，支持跨天事件)     │
│   标题字段: [任务名称 ▼]                         │
│   颜色标记: [状态 ▼] (可选)                       │
│   默认模式: [月视图 ▼]                            │
└─────────────────────────────────────────────────┘
```

字段选择器动态过滤：
- `dateField`: 仅显示 `date/datetime` 类型字段
- `endDateField`: 仅显示 `date/datetime` 类型字段
- `cardColorField`: 仅显示 `select/radio` 类型字段

---

## 实现范围

### P0 核心功能（必须实现）

- [ ] 类型定义扩展（CalendarConfig, ViewConfig）
- [ ] CalendarView.vue 组件创建
- [ ] 数据转换逻辑
- [ ] 月视图渲染
- [ ] 周视图渲染
- [ ] 卡片点击事件 → 打开详情对话框
- [ ] 颜色标记渲染
- [ ] DynamicPage.vue 集成
- [ ] 视图切换按钮增加日历图标

### P1 增强功能

- [ ] 拖拽移动日期
- [ ] 边缘拉伸调整时长
- [ ] 点击空白日期新增记录
- [ ] 配置界面（PageConfigManager.vue）

### P2 可选优化

- [ ] 日历工具栏本地化（中文）
- [ ] 今日高亮样式
- [ ] 事件过多时的折叠显示

---

## 测试要点

### 单元测试

- `transformToEvents` 函数：日期转换、空值过滤、颜色映射
- `useCalendarConfig` composable：配置解析、字段验证

### 集成测试

- 日历视图切换：表格 → 日历 → 周视图 → 月视图
- 卡片点击：正确打开详情对话框
- 拖拽更新：后端数据同步刷新

### E2E 测试

- 创建带日期字段的页面配置
- 启用日历视图
- 添加多条记录，验证日历显示
- 拖拽调整日期，验证数据更新