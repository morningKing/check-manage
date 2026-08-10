import { describe, it, expect, vi, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import SettingsSideMenu from '../SettingsSideMenu.vue'

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as never
})

const pushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock }),
  useRoute: () => ({ path: '/admin/users' }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))

let canImpl: (k: string) => boolean = () => true
vi.mock('@/stores', () => ({
  useAuthStore: () => ({ can: (k: string) => canImpl(k) }),
  useAppStore: () => ({ lastBusinessPath: '/page/orders' }),
}))

function mountMenu() {
  return mount(SettingsSideMenu)
}

describe('SettingsSideMenu', () => {
  it('全权限时渲染 7 个分组标题与 21 个条目链接', () => {
    canImpl = () => true
    const w = mountMenu()
    expect(w.findAll('.settings-menu__group-label')).toHaveLength(7)
    // 返回工作区是 <button>（非 RouterLink），不计入 'a' 选择器；21 个条目链接即总数。
    expect(w.findAll('a')).toHaveLength(21)
  })

  it('无权限的条目不渲染', () => {
    canImpl = k => k === 'admin.users'
    const w = mountMenu()
    const texts = w.findAll('a').map(a => a.text())
    expect(texts).toContain('用户管理')
    expect(texts).not.toContain('系统备份')
  })

  it('整组无权限时该组标题也不渲染', () => {
    canImpl = k => k === 'admin.users'
    const w = mountMenu()
    const labels = w.findAll('.settings-menu__group-label').map(e => e.text())
    expect(labels).toEqual(['访问控制'])
  })

  it('danger 条目带 danger class', () => {
    canImpl = () => true
    const w = mountMenu()
    const danger = w.find('.settings-menu__item--danger')
    expect(danger.exists()).toBe(true)
    expect(danger.text()).toContain('恢复出厂设置')
  })

  it('点返回工作区跳到 lastBusinessPath', async () => {
    canImpl = () => true
    pushMock.mockClear()
    const w = mountMenu()
    await w.find('.settings-menu__back').trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/page/orders')
  })
})
