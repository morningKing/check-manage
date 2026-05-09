<template>
  <div class="calendar-view">
    <FullCalendar
      ref="calendarRef"
      :options="calendarOptions"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import FullCalendar from '@fullcalendar/vue3'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import type { CalendarConfig, FieldConfig, DynamicRecord } from '@/types'
import type { EventClickArg, EventDropArg, CalendarApi } from '@fullcalendar/core'
import type { DateClickArg, EventResizeDoneArg } from '@fullcalendar/interaction'
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
function handleEventResize(arg: EventResizeDoneArg) {
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