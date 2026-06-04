import { get, post, put, del } from '@/utils/request'
import type { AiScanTask } from '@/types'

export function getScanTasks() { return get<AiScanTask[]>('/ai-scan-tasks') }
export function getScanTask(id: string) { return get<AiScanTask>(`/ai-scan-tasks/${id}`) }
export function createScanTask(data: Partial<AiScanTask>) { return post<AiScanTask>('/ai-scan-tasks', data) }
export function updateScanTask(id: string, data: Partial<AiScanTask>) { return put<AiScanTask>(`/ai-scan-tasks/${id}`, data) }
export function deleteScanTask(id: string) { return del(`/ai-scan-tasks/${id}`) }
export function runScanTaskNow(id: string) { return post<{ message: string }>(`/ai-scan-tasks/${id}/run-now`, {}) }
