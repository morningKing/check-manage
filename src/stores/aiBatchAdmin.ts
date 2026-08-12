import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { listAdminBatches, type AdminBatch, type AdminFilters } from '@/api/aiBatchAdmin'

/** 批任务的终态。轮询只在列表里还存在非终态项时才有意义。 */
const TERMINAL = ['completed', 'partial', 'failed']
const POLL_INTERVAL_MS = 10_000

/**
 * 管理态 store，与用户态的 aiChatBatches.ts **分开**。
 * 两者的过滤语义相反（一个跨用户、一个按归属），混用一个 store 会让
 * "当前是谁的视角"变成隐式状态。
 */
export const useAiBatchAdminStore = defineStore('aiBatchAdmin', () => {
  const items = ref<AdminBatch[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const loading = ref(false)
  const filters = reactive<AdminFilters>({ status: '', owner: '', source: '', keyword: '' })

  let timer: ReturnType<typeof setInterval> | null = null

  function hasNonTerminal() {
    return items.value.some(b => !TERMINAL.includes(b.status))
  }

  async function fetchList() {
    loading.value = true
    try {
      const clean: AdminFilters = {}
      for (const k of ['status', 'owner', 'source', 'keyword'] as const) {
        const v = (filters[k] || '').trim()
        if (v) clean[k] = v
      }
      const res = await listAdminBatches(clean, page.value, pageSize.value)
      items.value = res.items
      total.value = res.total
    } finally {
      loading.value = false
    }
  }

  /** 仅当当前页存在非终态批任务时才轮询 —— 管理页可能长时间开着无人查看，
   *  无条件轮询是白白消耗后端连接。 */
  function startPolling() {
    stopPolling()
    if (!hasNonTerminal()) return
    timer = setInterval(() => {
      if (!hasNonTerminal()) { stopPolling(); return }
      fetchList()
    }, POLL_INTERVAL_MS)
  }

  function stopPolling() {
    if (timer) { clearInterval(timer); timer = null }
  }

  return { items, total, page, pageSize, loading, filters,
           fetchList, startPolling, stopPolling, TERMINAL }
})
