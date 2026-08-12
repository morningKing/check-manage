import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { listAdminBatches, type AdminBatch, type AdminFilters } from '@/api/aiBatchAdmin'

/** 批任务的终态。轮询只在列表里还存在非终态项时才有意义。 */
const TERMINAL = ['completed', 'partial', 'failed']
const POLL_INTERVAL_MS = 10_000
/** 后台轮询连续失败达到这个次数就停止，避免权限中途被撤销 / token 过期 / 网络抖动
 *  时无限重试（每次都是 Unhandled Rejection，且旧快照会一直摆在表格里冒充实时数据）。 */
const POLL_FAILURE_THRESHOLD = 3

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
  /** 轮询连续失败达到阈值后置 true；页面据此渲染一条明确提示，而不是让表格
   *  继续展示旧快照假装数据是新的。手动 fetchList 成功后自动清除。 */
  const pollError = ref(false)

  let timer: ReturnType<typeof setInterval> | null = null
  let pollFailureCount = 0

  function hasNonTerminal() {
    return items.value.some(b => !TERMINAL.includes(b.status))
  }

  /** opts.silent：轮询发起的请求应静默失败（不弹全局 toast、不因 401 把用户踢到登录页
   *  —— 详见 src/utils/request.ts 顶部关于 silent 的约定）。用户主动点击的加载
   *  （首次加载、查询、翻页、重试后刷新）不传 silent，失败该有提示。 */
  async function fetchList(opts: { silent?: boolean } = {}) {
    loading.value = true
    try {
      const clean: AdminFilters = {}
      for (const k of ['status', 'owner', 'source', 'keyword'] as const) {
        const v = (filters[k] || '').trim()
        if (v) clean[k] = v
      }
      const res = opts.silent
        ? await listAdminBatches(clean, page.value, pageSize.value, { silent: true })
        : await listAdminBatches(clean, page.value, pageSize.value)
      items.value = res.items
      total.value = res.total
      pollFailureCount = 0
      pollError.value = false
    } finally {
      loading.value = false
    }
  }

  /** 仅当当前页存在非终态批任务时才轮询 —— 管理页可能长时间开着无人查看，
   *  无条件轮询是白白消耗后端连接。 */
  function startPolling() {
    stopPolling()
    if (!hasNonTerminal()) return
    pollFailureCount = 0
    timer = setInterval(() => {
      if (!hasNonTerminal()) { stopPolling(); return }
      fetchList({ silent: true }).catch(() => {
        pollFailureCount++
        if (pollFailureCount >= POLL_FAILURE_THRESHOLD) {
          pollError.value = true
          stopPolling()
        }
      })
    }, POLL_INTERVAL_MS)
  }

  function stopPolling() {
    if (timer) { clearInterval(timer); timer = null }
  }

  return { items, total, page, pageSize, loading, filters, pollError,
           fetchList, startPolling, stopPolling, TERMINAL }
})
