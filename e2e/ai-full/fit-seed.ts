/**
 * SkillOpt 拟合链路种子：定义文件 + attempt + manifest + agent_tool_calls 轨迹。
 * 轨迹事实源是 agent_tool_calls（skill_fit._load_trace），窗口 = attempt started..finished。
 */
import { execFileSync } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { dbSeed } from './batch/toolbox'
import { newId } from './db-helpers'

// package.json 带 "type": "module"，ESM 下无 __dirname——同 batch/toolbox.ts
// 以 import.meta.url 求模块目录（对 brief 原稿 __dirname 的等价替换，其余逐字一致）。
const DIRNAME = path.dirname(fileURLToPath(import.meta.url))

export const FIT_TITLE_PREFIX = 'AITEST-SKILLOPT-'
const madeDirs: string[] = []
// 定义名按「测试进程内共享」取值：定义是夹具、attempt 是它的任务（TC-FIT-01
// 断言 3 个 attempt 落在同一个 def_name 下：列表 hasLength(3) + summary tasks=3）。
// 首次 seedFitAttempt 生成后复用；cleanupFitSeeds 重置，下个用例重新生成。
// （对 brief 原稿「每次调用随机 defName」的偏差——原稿与用例断言自相矛盾，
// 用例代码是契约，种子按用例语义修正；worker 各持独立模块态，互不串扰。）
let sharedDefName: string | null = null

export function resolveWorkspaceRoot(): string {
  const serverDir = path.resolve(DIRNAME, '..', '..', 'server')
  const out = execFileSync('python', ['-c',
    `import sys; sys.path.insert(0, r'${serverDir}'); from config import AI_WORKSPACE_ROOT; print(AI_WORKSPACE_ROOT)`],
    { encoding: 'utf-8' })
  return out.trim()
}

export const TWO_STEPS = [
  { id: 'read_input', expect: [{ tool: 'read' }] },
  { id: 'save_result', expect: [{ tool: 'write' }] },
]

function writeSkillFile(root: string): { path: string; dir: string } {
  const dir = fs.mkdtempSync(path.join(root, 'AITEST-fit-'))
  madeDirs.push(dir)
  const p = path.join(dir, 'SKILL.md')
  const steps = TWO_STEPS.map(s =>
    `    - id: ${s.id}\n      expect:\n${s.expect.map(e => `        - tool: ${e.tool}\n`).join('')}`).join('')
  fs.writeFileSync(p, `---\nname: AITEST Fit Skill\nfit:\n  steps:\n${steps}---\n\n# AITEST fit skill body\n`)
  return { path: p, dir }
}

export interface FitSeed { attemptId: string; sessionId: string; defName: string; path: string; dir: string }

export async function seedFitAttempt(trace: string[]): Promise<FitSeed> {
  const root = resolveWorkspaceRoot()
  const { path: defPath, dir } = writeSkillFile(root)
  const sessionId = newId('sess_')
  dbSeed(`INSERT INTO ai_chat_sessions (id, user_id, title, status) VALUES ('${sessionId}', 'user-admin', '${FIT_TITLE_PREFIX}${sessionId}', 'completed')`)
  const attemptId = newId('att_')
  dbSeed(`INSERT INTO ai_execution_attempts (id, session_id, source_type, attempt_no, operation, status, started_at, finished_at)
          VALUES ('${attemptId}', '${sessionId}', 'interactive', 1, 'send', 'completed', NOW() - INTERVAL '1 hour', NOW())`)
  const content = fs.readFileSync(defPath)
  const hash = crypto.createHash('sha256').update(content).digest('hex')
  sharedDefName = sharedDefName ?? `AITEST-fit-${Date.now()}-${crypto.randomBytes(2).toString('hex')}`
  const defName = sharedDefName
  dbSeed(`INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, source, path, content_hash)
          VALUES ('${newId('man_')}', '${attemptId}', 'skill', '${defName}', 'session', '${defPath.replace(/\\/g, '/')}', '${hash}')`)
  const ocSid = 'ses_' + crypto.randomBytes(6).toString('hex')
  for (const [i, tool] of trace.entries()) {
    dbSeed(`INSERT INTO agent_tool_calls (oc_session_id, root_session_id, part_id, tool, args_text, state, occurred_at)
            VALUES ('${ocSid}', '${sessionId}', 'p${i}', '${tool}', '{"path":"a.csv"}', 'completed', NOW() - INTERVAL '30 minutes')`)
  }
  return { attemptId, sessionId, defName, path: defPath, dir }
}

export function cleanupFitSeeds(): void {
  dbSeed(`DELETE FROM ai_skill_fit_results WHERE def_name LIKE 'AITEST-fit-%'`)
  dbSeed(`DELETE FROM ai_skill_def_versions WHERE def_name LIKE 'AITEST-fit-%'`)
  dbSeed(`DELETE FROM agent_tool_calls WHERE root_session_id IN (SELECT id FROM ai_chat_sessions WHERE title LIKE '${FIT_TITLE_PREFIX}%')`)
  dbSeed(`DELETE FROM ai_skill_invocations WHERE session_id IN (SELECT id FROM ai_chat_sessions WHERE title LIKE '${FIT_TITLE_PREFIX}%')`)
  dbSeed(`DELETE FROM ai_execution_events WHERE attempt_id IN (SELECT a.id FROM ai_execution_attempts a JOIN ai_chat_sessions s ON s.id=a.session_id WHERE s.title LIKE '${FIT_TITLE_PREFIX}%')`)
  dbSeed(`DELETE FROM ai_execution_manifests WHERE attempt_id IN (SELECT a.id FROM ai_execution_attempts a JOIN ai_chat_sessions s ON s.id=a.session_id WHERE s.title LIKE '${FIT_TITLE_PREFIX}%')`)
  dbSeed(`DELETE FROM ai_execution_attempts WHERE session_id IN (SELECT id FROM ai_chat_sessions WHERE title LIKE '${FIT_TITLE_PREFIX}%')`)
  dbSeed(`DELETE FROM ai_chat_sessions WHERE title LIKE '${FIT_TITLE_PREFIX}%'`)
  sharedDefName = null
  for (const d of madeDirs.splice(0)) fs.rmSync(d, { recursive: true, force: true })
}
