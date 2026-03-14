/**
 * CommandPalette 组件单元测试
 *
 * 测试命令面板的搜索、键盘导航和最近访问功能
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

// Mock vue-router
const mockPush = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mockPush }),
  useRoute: () => ({ path: '/' }),
  createRouter: vi.fn(() => ({
    push: vi.fn(),
    beforeEach: vi.fn(),
    afterEach: vi.fn(),
    beforeResolve: vi.fn()
  })),
  createWebHistory: vi.fn()
}))

// Mock router modules
vi.mock('@/router/dynamicRoutes', () => ({
  generateRoutesFromMenus: vi.fn(() => []),
  addDynamicRoutes: vi.fn(),
  resetRouter: vi.fn()
}))

// Mock request utils
vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn()
}))

// Mock uuid
vi.mock('uuid', () => ({
  v4: () => 'mock-uuid-0001'
}))

import CommandPalette from '../CommandPalette.vue'
import { useMenuStore } from '@/stores/menu'
import { useTabStore } from '@/stores/tab'

// Stub Element Plus 组件
const stubs = {
  'el-icon': {
    template: '<span><slot /></span>'
  },
  'el-button': {
    template: '<button @click="$emit(\'click\')"><slot /></button>',
    emits: ['click']
  }
}

describe('CommandPalette', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockPush.mockClear()
    // 清理 body 中的 Teleport 内容
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  const mountComponent = async (visible = true) => {
    const wrapper = mount(CommandPalette, {
      props: {
        visible,
        'onUpdate:visible': (val: boolean) => {}
      },
      global: { stubs },
      attachTo: document.body
    })
    await nextTick()
    return wrapper
  }

  describe('基础渲染', () => {
    it('visible=true 时渲染面板', async () => {
      await mountComponent(true)
      const overlay = document.querySelector('.command-palette-overlay')
      expect(overlay).not.toBeNull()
      const palette = document.querySelector('.command-palette')
      expect(palette).not.toBeNull()
    })

    it('visible=false 时不渲染面板', async () => {
      await mountComponent(false)
      const overlay = document.querySelector('.command-palette-overlay')
      expect(overlay).toBeNull()
    })

    it('渲染搜索输入框', async () => {
      await mountComponent(true)
      const input = document.querySelector('.search-input')
      expect(input).not.toBeNull()
    })

    it('渲染底部提示', async () => {
      await mountComponent(true)
      const footer = document.querySelector('.footer-hint')
      expect(footer).not.toBeNull()
      expect(footer?.textContent).toContain('Enter')
      expect(footer?.textContent).toContain('Esc')
    })
  })

  describe('最近访问显示', () => {
    it('无搜索词时显示最近访问区域', async () => {
      await mountComponent(true)
      const sections = document.querySelectorAll('.section-header')
      expect(sections.length).toBeGreaterThan(0)
      expect(sections[0].textContent).toContain('最近访问')
    })

    it('有搜索词时不显示最近访问区域', async () => {
      await mountComponent(true)
      const input = document.querySelector('.search-input') as HTMLInputElement
      input.value = '测试'
      input.dispatchEvent(new Event('input'))
      await nextTick()

      const sections = document.querySelectorAll('.section-header')
      expect(sections[0].textContent).not.toContain('最近访问')
    })

    it('无最近访问时显示空提示', async () => {
      await mountComponent(true)
      const hint = document.querySelector('.empty-hint')
      expect(hint?.textContent).toContain('暂无最近访问记录')
    })
  })

  describe('键盘导航', () => {
    it('Escape 关闭面板', async () => {
      const wrapper = await mountComponent(true)
      const input = document.querySelector('.search-input') as HTMLInputElement

      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()

      expect(wrapper.emitted('update:visible')).toBeTruthy()
      expect(wrapper.emitted('update:visible')![0]).toEqual([false])
    })
  })
})

describe('CommandPalette - 拼音搜索', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    document.body.innerHTML = ''
  })

  it('getPinyinInitials 正确提取拼音首字母', async () => {
    const wrapper = mount(CommandPalette, {
      props: { visible: true, 'onUpdate:visible': () => {} },
      global: { stubs },
      attachTo: document.body
    })
    await nextTick()

    const vm = wrapper.vm as any
    expect(vm.getPinyinInitials?.('巡检用例')).toBe('xjyl')
    expect(vm.getPinyinInitials?.('系统管理')).toBe('xtgl')
    expect(vm.getPinyinInitials?.('ABC')).toBe('abc')
    expect(vm.getPinyinInitials?.('测试Test')).toBe('cstest')
  })
})