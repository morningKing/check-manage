import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getScanTasks, getScanTask, createScanTask, updateScanTask, deleteScanTask, runScanTaskNow } from '@/api/aiScanTask'
import type { AiScanTask } from '@/types'

export const useAiScanTaskStore = defineStore('aiScanTask', () => {
  const tasks = ref<AiScanTask[]>([])
  const loading = ref(false)
  async function load() {
    loading.value = true
    try { tasks.value = await getScanTasks() } finally { loading.value = false }
  }
  function fetchOne(id: string) { return getScanTask(id) }
  async function save(id: string, data: Partial<AiScanTask>) { await updateScanTask(id, data); await load() }
  async function add(data: Partial<AiScanTask>) { const t = await createScanTask(data); await load(); return t }
  async function remove(id: string) { await deleteScanTask(id); await load() }
  function runNow(id: string) { return runScanTaskNow(id) }
  return { tasks, loading, load, fetchOne, save, add, remove, runNow }
})
