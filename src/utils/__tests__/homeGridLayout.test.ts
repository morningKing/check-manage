import { describe, it, expect } from 'vitest'
import { layoutToGridStyle, isCustomHomeWidget } from '../homeGridLayout'

describe('layoutToGridStyle', () => {
  it('将起点在 0 的坐标转换为 CSS Grid 定位（CSS Grid 从 1 开始计数）', () => {
    expect(layoutToGridStyle({ x: 0, y: 0, w: 12, h: 4 })).toEqual({
      gridColumn: '1 / span 12',
      gridRow: '1 / span 4',
    })
  })

  it('非零起点正确偏移一位', () => {
    expect(layoutToGridStyle({ x: 6, y: 4, w: 6, h: 2 })).toEqual({
      gridColumn: '7 / span 6',
      gridRow: '5 / span 2',
    })
  })
})

describe('isCustomHomeWidget', () => {
  it('id 以 custom- 开头视为自定义区块', () => {
    expect(isCustomHomeWidget({ id: 'custom-chart-abc123', widgetType: 'chart' })).toBe(true)
  })

  it('内置区块（welcome/stats/quick-links/system-info）不是自定义区块', () => {
    expect(isCustomHomeWidget({ id: 'welcome', widgetType: 'welcome' })).toBe(false)
    expect(isCustomHomeWidget({ id: 'stats', widgetType: 'stats' })).toBe(false)
  })

  it('widgetType 属于可新增类型集合也视为自定义区块（兜底旧数据 id 不带 custom- 前缀的情况）', () => {
    expect(isCustomHomeWidget({ id: 'legacy-todo-1', widgetType: 'todo' })).toBe(true)
  })
})
