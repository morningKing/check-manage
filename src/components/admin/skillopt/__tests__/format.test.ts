import { describe, it, expect } from 'vitest'
import { fmtMs, pct } from '../format'

describe('fmtMs', () => {
  it('null/undefined → "-"', () => {
    expect(fmtMs(null)).toBe('-')
    expect(fmtMs(undefined)).toBe('-')
  })

  it('<1s 显示毫秒', () => {
    expect(fmtMs(500)).toBe('500ms')
  })

  it('<60s 显示一位小数秒', () => {
    expect(fmtMs(30000)).toBe('30.0s')
    // 59950ms = 59.95s，toFixed(1) 进位显示 60.0s——仍是秒分支合法输出
    expect(fmtMs(59950)).toBe('60.0s')
  })

  it('分钟分支不出现 m60s（进位边界钉死）', () => {
    expect(fmtMs(60000)).toBe('1m0s')
    expect(fmtMs(119500)).toBe('2m0s')   // 旧实现：1m60s（缺陷）
    expect(fmtMs(1799000)).toBe('29m59s')
  })

  it('PerfView 表格断言锚点：100000ms → 1m40s', () => {
    expect(fmtMs(100000)).toBe('1m40s')
  })
})

describe('pct', () => {
  it('null/undefined → "-"', () => {
    expect(pct(null)).toBe('-')
    expect(pct(undefined)).toBe('-')
  })

  it('四舍五入百分比', () => {
    expect(pct(0.6)).toBe('60%')
    expect(pct(0.595)).toBe('60%')
  })
})
