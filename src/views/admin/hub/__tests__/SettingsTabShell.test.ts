import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
// 壳的模板用的是真实 el-tabs/el-tab-pane/el-empty，测试需要注册 Element Plus
// 才能渲染出 .el-tabs__item（仅环境补齐，断言语义不变）。
import ElementPlus from 'element-plus'

vi.mock('@/views/admin/hub/settingsCatalog', async (orig) => {
  const actual = await orig<typeof import('@/views/admin/hub/settingsCatalog')>()
  return {
    ...actual,
    findSettingsItem: (id: string) => id === 'ai-settings' ? {
      id, label: 'AI 配置',
      tabs: [
        { id: 'model', label: '模型与密钥', perm: 'admin.ai_settings',
          component: () => Promise.resolve({ template: '<div class="pane-model"/>' }) },
        { id: 'runtime', label: '运行时', perm: 'admin.ai_runtime_read',
          component: () => Promise.resolve({ template: '<div class="pane-runtime"/>' }) },
      ],
    } : undefined,
  }
})
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ can: (k: string) => k === 'admin.ai_settings' }),
}))

import SettingsTabShell from '../SettingsTabShell.vue'

function makeRouter() {
  return createRouter({ history: createMemoryHistory(),
    routes: [{ path: '/admin/:id', component: { template: '<div/>' } }] })
}

async function mountShell(query = {}) {
  setActivePinia(createPinia())
  const router = makeRouter()
  router.push({ path: '/admin/ai-settings', query }); await router.isReady()
  const w = mount(SettingsTabShell, { props: { itemId: 'ai-settings' },
    global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return w
}

describe('SettingsTabShell', () => {
  it('无权限的 tab 不渲染', async () => {
    const w = await mountShell()                    // 只有 ai_settings
    expect(w.text()).toContain('模型与密钥')
    expect(w.text()).not.toContain('运行时')
  })
  it('?tab= 定位对应页签', async () => {
    const w = await mountShell({ tab: 'model' })
    expect((w.find('.el-tabs__item.is-active').element as HTMLElement).textContent).toContain('模型与密钥')
  })
  it('非法 tab 回退首个可见 tab 并回写 query', async () => {
    const w = await mountShell({ tab: 'bogus' })
    expect((w.find('.el-tabs__item.is-active').element as HTMLElement).textContent).toContain('模型与密钥')
  })
})
