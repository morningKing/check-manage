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

/** 与 HomeLayoutEditor.vue 里 GridLayout 的 col-num/row-height/margin 固定值保持一致 */
const GRID_COL_NUM = 12
const GRID_ROW_HEIGHT = 30
const GRID_MARGIN_X = 12
const GRID_MARGIN_Y = 12

/**
 * 像素坐标（相对网格容器左上角）→ 网格坐标（列/行）。
 *
 * 公式镜像自 grid-layout-plus 内部的 calcXY 实现
 * （node_modules/grid-layout-plus/es/components/grid-item.vue.mjs 约第 264-270 行），
 * 保证换算结果与库自身拖拽/缩放时的定位完全一致。
 *
 * @param left 相对网格容器左边缘的像素横坐标
 * @param top 相对网格容器上边缘的像素纵坐标
 * @param containerWidth 网格容器的实际像素宽度
 * @param w 待放置区块的网格列宽（用于把 x clamp 在 [0, 12-w] 内）
 */
export function pixelToGridPosition(
  left: number,
  top: number,
  containerWidth: number,
  w: number
): { x: number; y: number } {
  const colWidth = (containerWidth - GRID_MARGIN_X * (GRID_COL_NUM + 1)) / GRID_COL_NUM
  const rawX = Math.round((left - GRID_MARGIN_X) / (colWidth + GRID_MARGIN_X))
  const rawY = Math.round((top - GRID_MARGIN_Y) / (GRID_ROW_HEIGHT + GRID_MARGIN_Y))
  const x = Math.max(0, Math.min(rawX, GRID_COL_NUM - w))
  const y = Math.max(0, rawY)
  return { x, y }
}
