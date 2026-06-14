/**
 * 工作流引擎 store（管理员）
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { listWorkflows, saveWorkflow, deleteWorkflow, getInbox } from '@/api/workflow'
import type { WorkflowDefinition, WorkflowInboxItem } from '@/types/workflow'

export const useWorkflowStore = defineStore('workflow', () => {
  const definitions = ref<WorkflowDefinition[]>([])
  const inbox = ref<WorkflowInboxItem[]>([])
  const loading = ref(false)

  async function loadDefinitions(): Promise<void> {
    loading.value = true
    try {
      definitions.value = await listWorkflows()
    } finally {
      loading.value = false
    }
  }

  async function save(def: WorkflowDefinition): Promise<void> {
    await saveWorkflow(def)
    await loadDefinitions()
  }

  async function remove(id: string): Promise<void> {
    await deleteWorkflow(id)
    await loadDefinitions()
  }

  async function loadInbox(): Promise<void> {
    inbox.value = await getInbox()
  }

  return { definitions, inbox, loading, loadDefinitions, save, remove, loadInbox }
})
