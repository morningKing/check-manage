export interface EtlStepConfig {
  [key: string]: any
}

export interface EtlStep {
  id: string
  type: 'http_request' | 'json_input' | 'file_upload' | 'script' | 'field_mapping' | 'filter' | 'save_to_collection'
  name: string
  config: EtlStepConfig
  onError: 'stop' | 'skip'
}

export interface EtlTask {
  id: string
  name: string
  description: string
  steps: EtlStep[]
  enabled: boolean
  lastRunAt?: string
  lastRunStatus?: string
  createdAt: string
  updatedAt: string
}

export interface EtlStepResult {
  stepId: string
  stepName: string
  status: 'success' | 'error' | 'skipped'
  recordCount?: number
  error?: string
}

export interface EtlRunResult {
  status: string
  totalRecords: number
  successCount: number
  errorCount: number
  stepResults: EtlStepResult[]
  errors: string[]
}

export interface EtlLog {
  id: string
  taskId: string
  taskName: string
  status: string
  startedAt: string
  finishedAt?: string
  totalRecords: number
  successCount: number
  errorCount: number
  stepResults: EtlStepResult[]
  errorDetail?: string
}
