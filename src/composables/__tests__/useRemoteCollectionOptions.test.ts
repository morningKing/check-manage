import { describe, it, expect, vi, beforeEach } from 'vitest'

const getMock = vi.fn()
vi.mock('@/utils/request', () => ({ get: (...a: any[]) => getMock(...a) }))

import { useRemoteCollectionOptions } from '../useRemoteCollectionOptions'

function makeCfg(collection = 'cases', labelField = 'caseName') {
  return { collection: () => collection, labelField: () => labelField }
}

describe('useRemoteCollectionOptions', () => {
  beforeEach(() => {
    getMock.mockReset()
  })

  it('init: 取首屏选项(pageSize) + 回填已选 id(ids 过滤)，options 含两者', async () => {
    getMock.mockImplementation((_url: string, params: any) => {
      if (params?.ids) {
        // 回填已选：ids=sel-1
        return Promise.resolve({ data: [{ id: 'sel-1', caseName: '已选用例' }] })
      }
      // 首屏：前 N 条
      return Promise.resolve({ data: [{ id: 'a', caseName: '用例A' }, { id: 'b', caseName: '用例B' }] })
    })
    const c = useRemoteCollectionOptions(makeCfg())
    await c.init(['sel-1'])

    // 首屏用 pageSize、无 keyword
    expect(getMock).toHaveBeenCalledWith('/cases', { pageSize: 50 })
    // 回填用 ids + all
    expect(getMock).toHaveBeenCalledWith('/cases', { ids: 'sel-1', all: true })

    const values = c.options.value.map((o) => o.value)
    expect(values).toContain('sel-1')   // 已选(pinned)
    expect(values).toContain('a')       // 首屏
    expect(values).toContain('b')
    const sel = c.options.value.find((o) => o.value === 'sel-1')
    expect(sel?.label).toBe('已选用例')
  })

  it('onSearch: 防抖后用 keyword 查后端', async () => {
    vi.useFakeTimers()
    getMock.mockResolvedValue({ data: [{ id: 'x', caseName: '命中X' }] })
    const c = useRemoteCollectionOptions(makeCfg())
    c.onSearch('关键词')
    expect(getMock).not.toHaveBeenCalled()       // 防抖中
    await vi.advanceTimersByTimeAsync(350)
    expect(getMock).toHaveBeenCalledWith('/cases', { pageSize: 50, keyword: '关键词' })
    vi.useRealTimers()
    // 等微任务刷新 options
    await Promise.resolve()
    expect(c.options.value.map((o) => o.value)).toContain('x')
  })

  it('ensureSelectedLabels: 后端查不到的 id 退化为「裸 id 当标签」', async () => {
    getMock.mockResolvedValue({ data: [] })  // 该 id 已被删除/查不到
    const c = useRemoteCollectionOptions(makeCfg())
    await c.ensureSelectedLabels(['ghost-id'])
    const opt = c.options.value.find((o) => o.value === 'ghost-id')
    expect(opt).toBeTruthy()
    expect(opt?.label).toBe('ghost-id')   // 兜底
  })

  it('collection 为空时不发请求；已选值仍以裸 id 兜底显示', async () => {
    const c = useRemoteCollectionOptions({ collection: () => undefined, labelField: () => 'name' })
    await c.init(['x'])
    expect(getMock).not.toHaveBeenCalled()
    expect(c.options.value).toEqual([{ label: 'x', value: 'x' }])
  })
})
