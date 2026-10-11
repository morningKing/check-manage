/**
 * 门禁策略库（2026-10-11 spec §8）：命名的规则集，批引用即生效。
 */
import { get, post, patch, del } from '@/utils/request'

export type GateCheckMode = 'post' | 'pre' | 'checkpoint'

export interface GateCheckRow {
  name: string
  check_type?: 'tool' | 'file' | 'db_record' | 'verifier'
  tool?: string
  args_pattern?: string
  min_count?: number
  require_state?: string
  scope?: 'session' | 'tree'
  mode?: GateCheckMode
  timing?: 'before' | 'after'
  prompt?: string
  max_checks?: number
  assert_spec?: { tool: string; args_pattern: string; min_count: number } | null
  subagents?: string[] | null
  apply_to?: { batch_seq?: number[]; input_file_glob?: string } | null
  effect_spec?: Record<string, unknown> | null
  rubric?: string
}

export interface GatePolicy {
  id: string
  name: string
  description: string | null
  checks: GateCheckRow[]
  enabled: boolean
  createdBy?: string | null
  updatedAt?: string | null
}

export function listGatePolicies(enabled?: boolean) {
  return get<{ policies: GatePolicy[] }>(
    '/ai/chat/gate-policies', enabled === undefined ? undefined : { enabled: String(enabled) })
}
export function createGatePolicy(body: { name: string; description?: string; checks: GateCheckRow[]; enabled?: boolean }) {
  return post<GatePolicy>('/ai/chat/gate-policies', body)
}
export function updateGatePolicy(id: string, body: Partial<{ name: string; description: string; checks: GateCheckRow[]; enabled: boolean }>) {
  return patch<GatePolicy>(`/ai/chat/gate-policies/${encodeURIComponent(id)}`, body)
}
export function deleteGatePolicy(id: string) {
  return del<{ deleted: boolean }>(`/ai/chat/gate-policies/${encodeURIComponent(id)}`)
}
