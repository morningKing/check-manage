/**
 * AI 联动正确性套件共享 DB 种子助手。
 * 纪律：插值只允许自造值（AITEST 前缀 / uuid / 数字）；调用方负责 finally 清理。
 *
 * FK 说明（2026-10-06 实测 dev 库约束，与计划原稿的差异，均为自造 AITEST 父行）：
 * ai_chat_sessions.scan_task_id → ai_scan_tasks(id)、kefu_instance_id →
 * kefu_instances(id)、ai_chat_batches.api_key_id → api_keys(id) 都是真实外键，
 * 插假值直接 FK violation。故 scanTaskId/kefu/seedBatch(非空 apiKey) 会先种
 * 自造父行（AITEST-SESSV2 前缀），由 cleanupSessionsByPrefix /
 * cleanupBatchesByPrefix 兜底删除。
 */
import crypto from 'node:crypto'
import { dbSeed } from './batch/toolbox'

export const SESS_PREFIX = 'AITEST-SESSV2'

export function newId(prefix: string): string {
  return prefix + crypto.randomBytes(6).toString('hex')
}

export interface PlainSessionOpts {
  key: string
  userId?: string
  status?: string
  kind?: string | null
  batchId?: string | null
  scanTaskId?: boolean
  kefu?: boolean
  workspacePath?: string
  messages?: { role: 'user' | 'assistant'; text: string }[]
}

export async function seedPlainSession(o: PlainSessionOpts): Promise<string> {
  const sid = newId('sess_')
  const cols = ['id', 'user_id', 'title', 'status']
  const vals = [`'${sid}'`, `'${o.userId ?? 'user-admin'}'`,
    `'${SESS_PREFIX}-${o.key}'`, `'${o.status ?? 'completed'}'`]
  if (o.kind) { cols.push('kind'); vals.push(`'${o.kind}'`) }
  if (o.batchId) { cols.push('batch_id'); vals.push(`'${o.batchId}'`) }
  if (o.scanTaskId) {
    // FK：scan_task_id → ai_scan_tasks(id)，先种自造扫描任务父行
    const stid = newId(`${SESS_PREFIX}-scan-`)
    dbSeed(`INSERT INTO ai_scan_tasks (id, name, owner_user_id, collection, status_field, prompt_template) VALUES ('${stid}', '${SESS_PREFIX} scan seed', 'user-admin', 'AITEST', 'status', 'e2e seed')`)
    cols.push('scan_task_id'); vals.push(`'${stid}'`)
  }
  if (o.kefu) {
    // FK：kefu_instance_id → kefu_instances(id)，先种自造客服实例父行
    const kid = newId(`${SESS_PREFIX}-kefu-`)
    dbSeed(`INSERT INTO kefu_instances (id, slug, name, bot_user_id) VALUES ('${kid}', '${kid}', '${SESS_PREFIX} kefu seed', 'user-admin')`)
    cols.push('kefu_instance_id'); vals.push(`'${kid}'`)
  }
  if (o.workspacePath) { cols.push('workspace_path'); vals.push(`'${o.workspacePath.replace(/\\/g, '/')}'`) }
  dbSeed(`INSERT INTO ai_chat_sessions (${cols.join(',')}) VALUES (${vals.join(',')})`)
  for (const m of o.messages ?? []) {
    dbSeed(`INSERT INTO ai_chat_messages (id, session_id, role, content) VALUES ('${newId('msg_')}', '${sid}', '${m.role}', '[{"type":"text","text":"${m.text}"}]')`)
  }
  return sid
}

let batchSeq = 0
export function seedBatch(apiKey: string | null): string {
  batchSeq += 1
  const bid = newId('batch_')
  if (apiKey) {
    // FK：ai_chat_batches.api_key_id → api_keys(id)，先种自造密钥父行
    // （ON CONFLICT 容忍同一 key 复用；name 带 AITEST 前缀供清理兜底）
    dbSeed(`INSERT INTO api_keys (id, name, key_hash) VALUES ('${apiKey}', '${SESS_PREFIX}-ak-${batchSeq}', '${crypto.randomBytes(16).toString('hex')}') ON CONFLICT (id) DO NOTHING`)
  }
  dbSeed(`INSERT INTO ai_chat_batches (id, user_id, name, prompt, status, total, api_key_id) VALUES ('${bid}', 'user-admin', '${SESS_PREFIX}-batch-${batchSeq}', 'e2e seed', 'completed', 1, ${apiKey ? `'${apiKey}'` : 'NULL'})`)
  return bid
}

export function cleanupSessionsByPrefix(): void {
  dbSeed(`DELETE FROM ai_chat_sessions WHERE title LIKE '${SESS_PREFIX}-%'`)
  // 种子扫描/客服父行兜底清理（会话先删，父行随后；FK 均 ON DELETE SET NULL）
  dbSeed(`DELETE FROM ai_scan_tasks WHERE id LIKE '${SESS_PREFIX}-%'`)
  dbSeed(`DELETE FROM kefu_instances WHERE id LIKE '${SESS_PREFIX}-%'`)
}

export function cleanupBatchesByPrefix(): void {
  dbSeed(`DELETE FROM ai_chat_batches WHERE name LIKE '${SESS_PREFIX}-%'`)
  // 种子 api_keys 父行兜底清理（批行已删，无残留引用）
  dbSeed(`DELETE FROM api_keys WHERE name LIKE '${SESS_PREFIX}-%'`)
}
