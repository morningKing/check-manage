import { describe, it, expect } from 'vitest'
import { createRouter, createMemoryHistory } from 'vue-router'

const Stub = { template: '<div />' }
const routes = [
  { path: '/admin', component: Stub, redirect: '/admin/access', children: [
    { path: 'access', component: Stub, meta: { categoryId: 'access' } },
    { path: 'data-ops', component: Stub, meta: { categoryId: 'data-ops' } },
    { path: 'general', component: Stub, meta: { categoryId: 'general' } },
  ] },
  { path: '/admin/users', redirect: '/admin/access?tab=users' },
  { path: '/admin/query', redirect: '/admin/data-ops?tab=query' },
  { path: '/admin/system-settings', redirect: '/admin/general' },
]

describe('设置中心路由', () => {
  it('/admin 重定向到 /admin/access', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/admin'); await r.isReady()
    expect(r.currentRoute.value.path).toBe('/admin/access')
  })
  it('旧 /admin/users → /admin/access?tab=users', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/admin/users')
    expect(r.currentRoute.value.path).toBe('/admin/access')
    expect(r.currentRoute.value.query.tab).toBe('users')
  })
  it('旧 /admin/system-settings → /admin/general', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/admin/system-settings')
    expect(r.currentRoute.value.path).toBe('/admin/general')
  })
})
