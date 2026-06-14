/**
 * 工作流引擎 API
 */
import { get, post, del } from '@/utils/request'
import type { WorkflowDefinition, WorkflowInboxItem } from '@/types/workflow'

export const listWorkflows = () => get<WorkflowDefinition[]>('/workflow/definitions')
export const saveWorkflow = (d: WorkflowDefinition) => post<WorkflowDefinition>('/workflow/definitions', d)
export const deleteWorkflow = (id: string) => del(`/workflow/definitions/${id}`)
export const getInbox = () => get<WorkflowInboxItem[]>('/workflow/inbox')
export const startWorkflow = (workflowId: string, collection: string, recordId: string) =>
  post('/workflow/instances', { workflowId, collection, recordId })
