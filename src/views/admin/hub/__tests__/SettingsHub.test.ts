import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref, reactive } from 'vue'

const routeMock = reactive({ meta: { categoryId: 'access' }, path: '/admin/access' })
vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
  RouterLink: { template: '<a><slot /></a>' },
}))

const canRef = ref<(k: string) => boolean>(() => true)
vi.mock('@/stores', () => ({
  useAuthStore: () => ({ can: (k: string) => canRef.value(k) }),
}))

import SettingsHub from '../SettingsHub.vue'

const stubs = {
  RouterLink: { template: '<a class="rail-link"><slot /></a>' },
  'router-view': { template: '<div class="rv" />' },
  'el-icon': { template: '<i><slot /></i>' },
}

describe('SettingsHub', () => {
  it('超管：左栏渲染 7 个分类链接', () => {
    canRef.value = () => true
    const w = mount(SettingsHub, { global: { stubs } })
    expect(w.findAll('.rail-link')).toHaveLength(7)
  })

  it('仅 admin.users：左栏只剩访问控制 1 类', () => {
    canRef.value = (k: string) => k === 'admin.users'
    const w = mount(SettingsHub, { global: { stubs } })
    expect(w.findAll('.rail-link')).toHaveLength(1)
    expect(w.text()).toContain('访问控制')
  })

  it('无任何管理权限：左栏 0 个分类', () => {
    canRef.value = () => false
    const w = mount(SettingsHub, { global: { stubs } })
    expect(w.findAll('.rail-link')).toHaveLength(0)
  })
})
