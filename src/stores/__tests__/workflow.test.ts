import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, it, expect, vi } from 'vitest'
import type { WorkflowDefinition } from '@/types/workflow'

const saveWorkflow = vi.fn(() => Promise.resolve({} as WorkflowDefinition))
const listWorkflows = vi.fn(() =>
  Promise.resolve([{ id: 'w1', name: 'wf', enabled: true, stages: [] }] as WorkflowDefinition[]),
)

vi.mock('@/api/workflow', () => ({
  listWorkflows: (...a: unknown[]) => listWorkflows(...(a as [])),
  saveWorkflow: (...a: unknown[]) => saveWorkflow(...(a as [])),
  deleteWorkflow: vi.fn(() => Promise.resolve()),
  getInbox: vi.fn(() =>
    Promise.resolve([
      { instanceId: 'i1', workflowName: 'wf', stageName: 's1', collection: 'c', recordId: 'r', enteredAt: null },
    ]),
  ),
}))

import { useWorkflowStore } from '@/stores/workflow'

describe('workflow store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    listWorkflows.mockClear()
    saveWorkflow.mockClear()
  })

  it('loadDefinitions populates definitions', async () => {
    const s = useWorkflowStore()
    await s.loadDefinitions()
    expect(s.definitions).toHaveLength(1)
    expect(s.definitions[0].id).toBe('w1')
  })

  it('save calls saveWorkflow then reloads', async () => {
    const s = useWorkflowStore()
    const def: WorkflowDefinition = { name: 'new', enabled: true, stages: [] }
    await s.save(def)
    expect(saveWorkflow).toHaveBeenCalledWith(def)
    // reload populated state from listWorkflows
    expect(listWorkflows).toHaveBeenCalled()
    expect(s.definitions).toHaveLength(1)
  })

  it('loadInbox populates inbox', async () => {
    const s = useWorkflowStore()
    await s.loadInbox()
    expect(s.inbox).toHaveLength(1)
    expect(s.inbox[0].instanceId).toBe('i1')
  })
})
