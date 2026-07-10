import type { WidgetConfig, WidgetLayout, WidgetType } from '@/types'

/** 12 列网格坐标 → CSS Grid 定位样式（首页桌面态静态渲染用） */
export function layoutToGridStyle(layout: WidgetLayout): { gridColumn: string; gridRow: string } {
  return {
    gridColumn: `${layout.x + 1} / span ${layout.w}`,
    gridRow: `${layout.y + 1} / span ${layout.h}`,
  }
}

/** 可通过「首页配置」新增区块的类型（对应后端 allowed_types 白名单） */
const CREATABLE_WIDGET_TYPES: WidgetType[] = [
  'custom-markdown', 'data-card', 'quick-form',
  'chart', 'todo', 'activity', 'announcement',
]

/** 判断是否为「自定义区块」（可编辑标题/内容/可见性、可被删除，区别于四个内置区块） */
export function isCustomHomeWidget(widget: Pick<WidgetConfig, 'id' | 'widgetType'>): boolean {
  return widget.id.startsWith('custom-') || CREATABLE_WIDGET_TYPES.includes(widget.widgetType)
}
