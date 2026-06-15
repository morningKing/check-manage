import { get, post, put, del } from '@/utils/request'
import service from '@/utils/request'
import type { ExportScript } from '@/types'

export function getExportScripts() {
  return get<ExportScript[]>('/exportScripts')
}

export function createExportScript(data: Partial<ExportScript>) {
  return post<ExportScript>('/exportScripts', data)
}

export function updateExportScript(id: string, data: Partial<ExportScript>) {
  return put<ExportScript>(`/exportScripts/${id}`, data)
}

export function deleteExportScript(id: string) {
  return del(`/exportScripts/${id}`)
}

export function testExportScript(id: string, testData: {
  collection?: string
  branchId?: string
  data?: any[]
  fields?: any[]
  pageName?: string
}) {
  return post<{ success: boolean; preview: string; filename: string; contentType: string; size: number; recordCount?: number; extra?: string; error?: string }>(
    `/exportScripts/${id}/test`,
    testData
  )
}

export interface DebugSnapshot {
  line: number
  vars: Record<string, any>
}

export interface DebugResult {
  success: boolean
  error?: string | null
  errorLine?: number | null
  breakpointHits: DebugSnapshot[]
  lineHits: Record<string, number>
  resultPreview?: string
}

export function debugExportScript(id: string, payload: {
  breakpoints: number[]
  collection?: string
  branchId?: string
  menuId?: string
  data?: any[]
  fields?: any[]
  pageName?: string
}) {
  return post<DebugResult>(`/exportScripts/${id}/debug`, payload)
}

export async function executeExportScript(scriptId: string, collection: string, recordId?: string) {
  const payload: Record<string, string> = { scriptId, collection }
  if (recordId) payload.recordId = recordId
  const response = await service.post('/exportScripts/execute', payload, {
    responseType: 'blob',
  })

  const res = response as any
  const disposition = res.headers?.['content-disposition'] || ''

  // If no Content-Disposition header, this is not a file download — likely a JSON error
  if (!disposition) {
    const contentType = res.headers?.['content-type'] || ''
    if (contentType.includes('application/json')) {
      const text = await (res.data as Blob).text()
      const json = JSON.parse(text)
      throw new Error(json.error || '导出失败')
    }
  }

  const blob = new Blob([res.data])
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url

  // Parse filename from Content-Disposition header
  // Supports both: filename=name.ext and filename*=UTF-8''encoded_name.ext
  let downloadName = 'export.dat'
  const starMatch = disposition.match(/filename\*=UTF-8''(.+)/i)
  if (starMatch) {
    downloadName = decodeURIComponent(starMatch[1])
  } else {
    const plainMatch = disposition.match(/filename=(.+)/)
    if (plainMatch) {
      downloadName = decodeURIComponent(plainMatch[1])
    }
  }
  link.download = downloadName

  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}

export interface BatchExportTask {
  scriptId: string
  collection: string
}

export async function executeBatchExport(tasks: BatchExportTask[]) {
  const response = await service.post('/exportScripts/batchExport', { tasks }, {
    responseType: 'blob',
    timeout: 120000,
  })

  const res = response as any
  const disposition = res.headers?.['content-disposition'] || ''

  if (!disposition) {
    const contentType = res.headers?.['content-type'] || ''
    if (contentType.includes('application/json')) {
      const text = await (res.data as Blob).text()
      const json = JSON.parse(text)
      throw new Error(json.error || '批量导出失败')
    }
  }

  const blob = new Blob([res.data])
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url

  let downloadName = '批量导出.zip'
  const starMatch = disposition.match(/filename\*=UTF-8''(.+)/i)
  if (starMatch) {
    downloadName = decodeURIComponent(starMatch[1])
  }
  link.download = downloadName

  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}
