import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  listSessions, getSessionDetail, getSessionMessages, getSessionFiles, archiveSession,
  type AdminSession, type AdminSessionDetail, type AdminSessionMessage,
  type AdminSessionFile, type AdminSessionFilters,
} from '@/api/aiSessionAdmin'

export const useAiSessionAdminStore = defineStore('aiSessionAdmin', () => {
  // List state
  const items = ref<AdminSession[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const loading = ref(false)
  const filters = ref<AdminSessionFilters>({})
  const pollError = ref<string | null>(null)

  // Detail drawer state
  const detailVisible = ref(false)
  const detail = ref<AdminSessionDetail | null>(null)
  const detailLoading = ref(false)
  const messages = ref<AdminSessionMessage[]>([])
  const messagesTotal = ref(0)
  const messagesTruncated = ref(false)
  const messagesLoading = ref(false)
  const files = ref<AdminSessionFile[]>([])
  const filesLoading = ref(false)
  const activeTab = ref<'info' | 'messages' | 'files'>('info')

  // Polling
  let pollTimer: ReturnType<typeof setInterval> | null = null
  const pollFailCount = ref(0)
  const POLL_INTERVAL = 8000
  const POLL_FAIL_THRESHOLD = 3

  const hasRunning = computed(() =>
    items.value.some(i => ['pending', 'running'].includes(i.status)),
  )

  async function fetchList() {
    loading.value = true
    try {
      const res = await listSessions(filters.value, page.value, pageSize.value)
      items.value = res.items
      total.value = res.total
      pollError.value = null
      pollFailCount.value = 0
    } catch (e: any) {
      pollError.value = e?.message || '加载失败'
      pollFailCount.value++
    } finally {
      loading.value = false
    }
  }

  function startPolling() {
    stopPolling()
    pollTimer = setInterval(() => {
      if (pollFailCount.value >= POLL_FAIL_THRESHOLD) {
        stopPolling()
        return
      }
      fetchList()
    }, POLL_INTERVAL)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function openDetail(sessionId: string) {
    detailVisible.value = true
    detailLoading.value = true
    activeTab.value = 'info'
    messages.value = []
    files.value = []
    try {
      detail.value = await getSessionDetail(sessionId)
    } catch {
      detail.value = null
    } finally {
      detailLoading.value = false
    }
  }

  function closeDetail() {
    detailVisible.value = false
    detail.value = null
    messages.value = []
    files.value = []
  }

  async function fetchMessages() {
    if (!detail.value) return
    messagesLoading.value = true
    try {
      const res = await getSessionMessages(detail.value.id)
      messages.value = res.messages
      messagesTotal.value = res.total
      messagesTruncated.value = res.truncated
    } catch {
      messages.value = []
    } finally {
      messagesLoading.value = false
    }
  }

  async function fetchFiles() {
    if (!detail.value) return
    filesLoading.value = true
    try {
      const res = await getSessionFiles(detail.value.id)
      files.value = res.files
    } catch {
      files.value = []
    } finally {
      filesLoading.value = false
    }
  }

  async function doArchive(sessionId: string) {
    await archiveSession(sessionId)
    await fetchList()
  }

  function setFilter(key: keyof AdminSessionFilters, value: string) {
    filters.value = { ...filters.value, [key]: value || undefined }
    page.value = 1
  }

  function clearFilters() {
    filters.value = {}
    page.value = 1
  }

  return {
    items, total, page, pageSize, loading, filters, pollError,
    hasRunning,
    detailVisible, detail, detailLoading,
    messages, messagesTotal, messagesTruncated, messagesLoading,
    files, filesLoading, activeTab,
    fetchList, startPolling, stopPolling,
    openDetail, closeDetail, fetchMessages, fetchFiles,
    doArchive, setFilter, clearFilters,
  }
})
