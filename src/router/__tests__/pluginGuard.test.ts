/**
 * 插件路由门测试（plugin-architecture spec §5）
 *
 * 覆盖：
 * - 无 meta.plugin 恒放行，且不触碰 plugins store
 * - meta.plugin 且插件启用 → 放行
 * - meta.plugin 且插件禁用 → 拦截（false，由守卫转 NotFound）
 * - ensureLoaded reject → 放行（fail-open：后端蓝图门才是硬墙）
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { RouteLocationNormalized } from 'vue-router'

// mock @/stores/plugins：pluginGuard 走动态 import，vi.mock 同样拦截
const { ensureLoadedMock, enabledMock, usePluginsStoreMock } = vi.hoisted(() => ({
  ensureLoadedMock: vi.fn(),
  enabledMock: vi.fn(),
  usePluginsStoreMock: vi.fn(),
}))

vi.mock('@/stores/plugins', () => ({
  usePluginsStore: usePluginsStoreMock,
}))

import { pluginRouteAllowed } from '../pluginGuard'

function makeRoute(meta: Record<string, unknown>): RouteLocationNormalized {
  return { meta } as unknown as RouteLocationNormalized
}

describe('pluginRouteAllowed', () => {
  beforeEach(() => {
    usePluginsStoreMock.mockReset().mockImplementation(() => ({
      ensureLoaded: ensureLoadedMock,
      enabled: enabledMock,
    }))
    ensureLoadedMock.mockReset().mockResolvedValue(undefined)
    enabledMock.mockReset().mockReturnValue(true)
  })

  it('无 meta.plugin 恒放行，且不触碰 store', async () => {
    const result = await pluginRouteAllowed(makeRoute({ title: '首页' }))

    expect(result).toBe(true)
    expect(usePluginsStoreMock).not.toHaveBeenCalled()
    expect(ensureLoadedMock).not.toHaveBeenCalled()
  })

  it('meta.plugin 且插件启用 → 放行', async () => {
    enabledMock.mockReturnValue(true)

    const result = await pluginRouteAllowed(makeRoute({ plugin: 'smart-writing' }))

    expect(result).toBe(true)
    expect(ensureLoadedMock).toHaveBeenCalledTimes(1)
    expect(enabledMock).toHaveBeenCalledWith('smart-writing')
  })

  it('meta.plugin 且插件禁用 → 拦截', async () => {
    enabledMock.mockReturnValue(false)

    const result = await pluginRouteAllowed(makeRoute({ plugin: 'smart-writing' }))

    expect(result).toBe(false)
  })

  it('ensureLoaded reject → 放行（fail-open）', async () => {
    ensureLoadedMock.mockRejectedValue(new Error('GET /plugins 5xx'))
    // 即使 enabled=false 也不该被走到：fail-open 优先于禁用判断
    enabledMock.mockReturnValue(false)

    const result = await pluginRouteAllowed(makeRoute({ plugin: 'smart-writing' }))

    expect(result).toBe(true)
    expect(enabledMock).not.toHaveBeenCalled()
  })
})
