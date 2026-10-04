import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import type { Router } from 'vue-router'
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

// 授权态可按用例覆写：默认放行两个权限，让「模型与密钥 / 运行时」同时可见——
// 这样 ?tab=runtime 深链才能与「默认进首个可见页签」区分开（有鉴别力）；
// 「无权限的 tab 不渲染」用例单独收紧为单权限。
const grant = vi.hoisted(() => ({ perms: ['admin.ai_settings', 'admin.ai_runtime_read'] as string[] }))
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ can: (k: string) => grant.perms.includes(k) }),
}))

import SettingsTabShell from '../SettingsTabShell.vue'

const ALL_PERMS = ['admin.ai_settings', 'admin.ai_runtime_read']

function makeRouter() {
  return createRouter({ history: createMemoryHistory(),
    routes: [{ path: '/admin/:id', component: { template: '<div/>' } }] })
}

async function mountShell(query: Record<string, string> = {}, perms: string[] = ALL_PERMS) {
  grant.perms = perms   // 每次重置授权态，避免用例间泄漏
  setActivePinia(createPinia())
  const router = makeRouter()
  router.push({ path: '/admin/ai-settings', query }); await router.isReady()
  const w = mount(SettingsTabShell, { props: { itemId: 'ai-settings' },
    global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return w
}

// 从壳的 el-tabs 头部按文案找页签项（子组件内可能嵌套 el-tabs，故锚定壳层 header）
function findTab(w: Awaited<ReturnType<typeof mountShell>>, label: string) {
  const tab = w.findAll('.settings-tab-shell > .el-tabs__header .el-tabs__item')
    .find(el => el.text().includes(label))
  expect(tab, `页签「${label}」应渲染`).toBeTruthy()
  return tab!
}

describe('SettingsTabShell', () => {
  it('无权限的 tab 不渲染', async () => {
    const w = await mountShell({}, ['admin.ai_settings'])   // 只放行 ai_settings
    expect(w.text()).toContain('模型与密钥')
    expect(w.text()).not.toContain('运行时')
  })
  it('?tab= 深链定位到非默认页签', async () => {
    // 两个页签都可见，深链指向第二个：默认激活的是「模型与密钥」，
    // 断言「运行时」激活才证明 query 真正参与了定位，而非与默认行为重合。
    const w = await mountShell({ tab: 'runtime' })
    expect((w.find('.el-tabs__item.is-active').element as HTMLElement).textContent).toContain('运行时')
  })
  it('非法 tab 回退首个可见 tab', async () => {
    // 注：进页时组件不会自愈 URL（回写 query 只发生在切换页签时），
    // 故这里只断言激活页签回落，不断言地址栏。
    const w = await mountShell({ tab: 'bogus' })
    expect((w.find('.el-tabs__item.is-active').element as HTMLElement).textContent).toContain('模型与密钥')
  })
  it('点击页签回写 query.tab', async () => {
    const w = await mountShell()
    const router = (w.vm as { $router: Router }).$router
    // 组件设计：进页无 query 不补写地址栏，仅切换页签时回写
    expect(router.currentRoute.value.query.tab).toBeUndefined()
    await findTab(w, '运行时').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.tab).toBe('runtime')
  })
  it('懒挂载：未激活 pane 内容不在 DOM，激活后出现', async () => {
    const w = await mountShell()
    expect(w.find('.pane-runtime').exists()).toBe(false)
    await findTab(w, '运行时').trigger('click')
    await flushPromises()   // 异步组件解析
    expect(w.find('.pane-runtime').exists()).toBe(true)
  })
})
