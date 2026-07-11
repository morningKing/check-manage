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

import { pixelToGridPosition } from '../homeGridLayout'

describe('pixelToGridPosition', () => {
  // containerWidth=900, colNum=12, marginX=12 => colWidth = (900 - 12*13) / 12 = 62
  it('容器左上角 (0,0) 换算为网格 (0,0)', () => {
    expect(pixelToGridPosition(0, 0, 900, 6)).toEqual({ x: 0, y: 0 })
  })

  it('落在第 2 列（一个 colWidth+marginX 之外）换算为 x=1', () => {
    // left = colWidth + marginX = 62 + 12 = 74
    expect(pixelToGridPosition(74, 0, 900, 6)).toEqual({ x: 1, y: 0 })
  })

  it('落在第 2 行（一个 rowHeight+marginY 之外）换算为 y=1', () => {
    // top = rowHeight + marginY = 30 + 12 = 42
    expect(pixelToGridPosition(0, 42, 900, 6)).toEqual({ x: 0, y: 1 })
  })

  it('超出右边界时 x 被 clamp 到 12-w', () => {
    expect(pixelToGridPosition(10000, 0, 900, 6)).toEqual({ x: 6, y: 0 })
  })

  it('负坐标时 x/y 被 clamp 到 0', () => {
    expect(pixelToGridPosition(-100, -100, 900, 6)).toEqual({ x: 0, y: 0 })
  })
})
