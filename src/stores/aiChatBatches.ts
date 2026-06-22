import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '@/api/aiChatBatches'
import type {
  AiChatBatch, AiChatBatchDetail, AiChatBatchSession,
} from '@/types/aiChatBatch'

const TERMINAL_STATUSES = new Set(['completed', 'failed'])
const DETAIL_POLL_MS = 5000
const LIST_POLL_MS = 10000

export const useAiChatBatchesStore = defineStore('aiChatBatches', () => {
  const items = ref<AiChatBatch[]>([])
  const activeBatch = ref<AiChatBatch | null>(null)
  const activeSessions = ref<AiChatBatchSession[]>([])
  const polling = ref(false)
  const listPolling = ref(false)

  let detailTimer: ReturnType<typeof setTimeout> | null = null
  let listTimer: ReturnType<typeof setTimeout> | null = null

  async function fetchList() {
    const { items: rows } = await api.listBatches()
    items.value = rows
  }

  function startListPolling() {
    if (listPolling.value) return
    listPolling.value = true
    const tick = async () => {
      if (!listPolling.value) return
      try { await fetchList() } catch { /* swallow during polling */ }
      listTimer = setTimeout(tick, LIST_POLL_MS)
    }
    listTimer = setTimeout(tick, LIST_POLL_MS)
  }

  function stopListPolling() {
    listPolling.value = false
    if (listTimer) { clearTimeout(listTimer); listTimer = null }
  }

  async function selectBatch(id: string) {
    const detail = await api.getBatch(id)
    applyDetail(detail)
    if (!TERMINAL_STATUSES.has(detail.batch.status)) {
      startDetailPolling(id)
    }
  }

  function applyDetail(detail: AiChatBatchDetail) {
    activeBatch.value = detail.batch
    activeSessions.value = detail.sessions
  }

  function startDetailPolling(id: string) {
    stopDetailPolling()
    polling.value = true
    const tick = async () => {
      if (!polling.value) return
      try {
        const detail = await api.getBatch(id)
        applyDetail(detail)
        if (TERMINAL_STATUSES.has(detail.batch.status)) {
          stopDetailPolling()
          return
        }
      } catch { /* swallow */ }
      detailTimer = setTimeout(tick, DETAIL_POLL_MS)
    }
    detailTimer = setTimeout(tick, DETAIL_POLL_MS)
  }

  function stopDetailPolling() {
    polling.value = false
    if (detailTimer) { clearTimeout(detailTimer); detailTimer = null }
  }

  function clearSelection() {
    stopDetailPolling()
    activeBatch.value = null
    activeSessions.value = []
  }

  async function retryFailed() {
    if (!activeBatch.value) return
    const id = activeBatch.value.id
    activeBatch.value.failed = 0   // optimistic
    await api.retryFailedSessions(id)
    // refetch authoritative state and resume polling
    const detail = await api.getBatch(id)
    applyDetail(detail)
    if (!TERMINAL_STATUSES.has(detail.batch.status)) startDetailPolling(id)
  }

  async function createAndSelect(body: Parameters<typeof api.createBatch>[0]) {
    const detail = await api.createBatch(body)
    applyDetail(detail)
    items.value = [detail.batch, ...items.value]
    if (!TERMINAL_STATUSES.has(detail.batch.status)) {
      startDetailPolling(detail.batch.id)
    }
    return detail
  }

  async function removeBatch(id: string) {
    await api.deleteBatch(id)
    items.value = items.value.filter(b => b.id !== id)
    if (activeBatch.value?.id === id) clearSelection()
  }

  async function appendToBatch(id: string, files: { name: string; path: string }[]) {
    const detail = await api.appendBatch(id, files)
    await fetchList()
    if (activeBatch.value?.id === id) {
      applyDetail(detail)
      if (!TERMINAL_STATUSES.has(detail.batch.status)) startDetailPolling(id)
    }
    return detail
  }

  // Tab-visibility pause: when the page is hidden, stop both polling loops to
  // save bandwidth and battery; resume when visible (refetching once first so
  // the UI is up-to-date the moment the user returns).
  let pausedDetailId: string | null = null
  let pausedList = false
  function attachVisibilityHandler() {
    if (typeof document === 'undefined') return
    document.addEventListener('visibilitychange', async () => {
      if (document.hidden) {
        pausedDetailId = activeBatch.value?.id ?? null
        pausedList = listPolling.value
        stopDetailPolling()
        stopListPolling()
      } else {
        if (pausedList) { await fetchList(); startListPolling() }
        if (pausedDetailId) {
          try {
            const d = await api.getBatch(pausedDetailId)
            applyDetail(d)
            if (!TERMINAL_STATUSES.has(d.batch.status)) startDetailPolling(pausedDetailId)
          } catch { /* swallow */ }
        }
        pausedDetailId = null
        pausedList = false
      }
    })
  }
  attachVisibilityHandler()

  return {
    items, activeBatch, activeSessions, polling, listPolling,
    fetchList, startListPolling, stopListPolling,
    selectBatch, clearSelection, retryFailed,
    createAndSelect, removeBatch, appendToBatch,
  }
})
