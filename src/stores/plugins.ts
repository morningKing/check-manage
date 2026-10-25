/**
 * 第一方插件 store（plugin-architecture spec §5 前端挂载点）
 *
 * 数据源唯一 GET /plugins；ensureLoaded 单飞；导航守卫 fail-open（后端门是硬墙）。
 */

import { defineStore } from 'pinia'
import { getPlugins } from '@/api/plugins'

export const usePluginsStore = defineStore('plugins', {
  state: () => ({
    enabledSet: new Set<string>(),
    loaded: false,
    _pending: null as Promise<void> | null,
  }),
  actions: {
    async ensureLoaded(): Promise<void> {
      if (this.loaded) return
      if (!this._pending) {
        this._pending = getPlugins()
          .then((list) => {
            this.enabledSet = new Set((list ?? []).filter((p) => p.enabled).map((p) => p.id))
            this.loaded = true
          })
          .finally(() => {
            this._pending = null
          })
      }
      return this._pending
    },
    enabled(id: string): boolean {
      return this.enabledSet.has(id)
    },
    async refresh(): Promise<void> {
      this.loaded = false
      await this.ensureLoaded()
    },
  },
})
