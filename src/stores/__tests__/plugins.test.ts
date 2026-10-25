/**
 * 插件 store 测试（plugin-architecture spec §5 前端挂载点）
 *
 * 覆盖：
 * - ensureLoaded 单飞：并发两次只发一次 GET /plugins
 * - ensureLoaded 已加载后不再重复请求（缓存）
 * - enabled(id) 按 list 的 enabled 字段过滤
 * - refresh() 强制重拉
 * - 加载失败：reject 透出、loaded 保持 false（可重试）
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// mock @/api/plugins：getPlugins 可控，store 行为不依赖网络
const { getPluginsMock } = vi.hoisted(() => ({
  getPluginsMock: vi.fn(),
}))

vi.mock('@/api/plugins', () => ({
  getPlugins: getPluginsMock,
  setPluginEnabled: vi.fn(),
}))

import { usePluginsStore } from '../plugins'
import type { PluginInfo } from '@/api/plugins'

function makePlugin(id: string, enabled: boolean): PluginInfo {
  return { id, name: id, description: '', version: '0.0.1', enabled }
}

describe('Plugins Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    getPluginsMock.mockReset()
  })

  it('ensureLoaded 并发两次只发一次请求（单飞）', async () => {
    // 用手动 resolve 的 promise 把首次请求挂住，期间发起第二次调用
    let resolveFetch!: (list: PluginInfo[]) => void
    getPluginsMock.mockImplementation(
      () => new Promise<PluginInfo[]>((resolve) => { resolveFetch = resolve })
    )

    const store = usePluginsStore()
    const p1 = store.ensureLoaded()
    const p2 = store.ensureLoaded()

    expect(getPluginsMock).toHaveBeenCalledTimes(1)

    resolveFetch([makePlugin('smart-writing', true)])
    await Promise.all([p1, p2])

    expect(store.loaded).toBe(true)
    expect(store.enabled('smart-writing')).toBe(true)
  })

  it('ensureLoaded 已加载后不再重复请求（缓存）', async () => {
    getPluginsMock.mockResolvedValue([makePlugin('smart-writing', true)])

    const store = usePluginsStore()
    await store.ensureLoaded()
    await store.ensureLoaded()

    expect(getPluginsMock).toHaveBeenCalledTimes(1)
  })

  it('enabled() 按 list 的 enabled 字段过滤', async () => {
    getPluginsMock.mockResolvedValue([
      makePlugin('smart-writing', true),
      makePlugin('other-plugin', false),
    ])

    const store = usePluginsStore()
    await store.ensureLoaded()

    expect(store.enabled('smart-writing')).toBe(true)
    expect(store.enabled('other-plugin')).toBe(false)
    expect(store.enabled('unknown-plugin')).toBe(false)
  })

  it('refresh() 强制重拉（loaded 也会被新结果覆盖）', async () => {
    getPluginsMock.mockResolvedValueOnce([makePlugin('smart-writing', true)])
    const store = usePluginsStore()
    await store.ensureLoaded()
    expect(store.enabled('smart-writing')).toBe(true)

    // 后端把插件关了，refresh 应拿到新状态
    getPluginsMock.mockResolvedValueOnce([makePlugin('smart-writing', false)])
    await store.refresh()

    expect(getPluginsMock).toHaveBeenCalledTimes(2)
    expect(store.enabled('smart-writing')).toBe(false)
    expect(store.loaded).toBe(true)
  })

  it('加载失败 reject 且 loaded 保持 false（可重试）', async () => {
    getPluginsMock.mockRejectedValueOnce(new Error('network down'))

    const store = usePluginsStore()
    await expect(store.ensureLoaded()).rejects.toThrow('network down')

    expect(store.loaded).toBe(false)

    // 重试成功（说明失败后 _pending 已复位，没有卡死）
    getPluginsMock.mockResolvedValueOnce([makePlugin('smart-writing', true)])
    await store.ensureLoaded()

    expect(getPluginsMock).toHaveBeenCalledTimes(2)
    expect(store.loaded).toBe(true)
    expect(store.enabled('smart-writing')).toBe(true)
  })
})
