export interface FieldMappingRow { jsonKey: string; column: string; required: boolean }
export interface AiScanTask {
  id: string
  name: string
  enabled: boolean
  ownerUserId?: string
  collection: string
  branchId: string
  statusField: string
  pendingValue: string
  runningValue: string
  doneValue: string
  failedValue: string
  extraFilter: Record<string, unknown>
  contextFields: Record<string, unknown>
  promptTemplate: string
  fieldMapping: FieldMappingRow[]
  scheduleIntervalMinutes: number
  maxRecordsPerScan: number
  lastRunAt?: string | null
  lastScanCount?: number
  lastError?: string | null
}
