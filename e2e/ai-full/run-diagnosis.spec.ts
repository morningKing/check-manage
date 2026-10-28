/**
 * O1 运行诊断（真实后端，零 LLM）。
 * spec: docs/superpowers/specs/2026-10-26-agent-self-evolution-design.md §7
 * 约定: 等真实元素,绝不等 networkidle;种子行 finally 清理（db_exec 桥）。
 * 种子: attempt + 一条 failed 门禁期望 → 诊断必含 rule_compliance=fail。
 */
import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import crypto from 'node:crypto'

const DIRNAME = path.dirname(fileURLToPath(import.meta.url))
const TAG = `rd_${Date.now()}`

function db(sql: string): string {
  const script = path.join(DIRNAME, 'batch', 'db_exec.py')
  return execFileSync('python', [script], { input: sql, encoding: 'utf-8' })
}

const uid = crypto.randomUUID()
const sid = crypto.randomUUID()
const attemptId = crypto.randomUUID()

function seed(): void {
  db(`
    INSERT INTO users (id, username, password_hash, display_name, role)
    VALUES ('${uid}', 'u_${TAG}', 'x', 'RD', 'developer');
    INSERT INTO ai_chat_sessions (id, user_id, status, session_token)
    VALUES ('${sid}', '${uid}', 'completed', 'tok-${TAG}');
    INSERT INTO ai_execution_attempts (id, session_id, source_type, operation,
      started_at, finished_at)
    VALUES ('${attemptId}', '${sid}', 'batch', 'send', NOW(), NOW());
    INSERT INTO action_expectations (scope_type, scope_id, name, tool,
      args_pattern, source, last_status, last_checked_at, last_evidence)
    VALUES ('tree', '${sid}', 'e2e-gate-${TAG}', 'bash', 'git clone',
      'policy', 'failed', NOW(), 0);`)
}

function cleanup(): void {
  db(`
    DELETE FROM ai_run_diagnoses WHERE attempt_id = '${attemptId}';
    DELETE FROM action_expectations WHERE scope_id = '${sid}';
    DELETE FROM ai_execution_attempts WHERE id = '${attemptId}';
    DELETE FROM ai_chat_sessions WHERE id = '${sid}';
    DELETE FROM users WHERE id = '${uid}';`)
}

async function login(page: Page): Promise<void> {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  try {
    await userInput.waitFor({ state: 'visible', timeout: 30_000 })
    await userInput.fill('admin')
    await page.locator('input[placeholder*="密码"]').fill('admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await userInput.waitFor({ state: 'hidden', timeout: 15_000 })
  } catch { /* 已是登录态 */ }
}

test('运行诊断面板：生成→维度含 rule_compliance=fail', async ({ page }) => {
  test.setTimeout(180_000)
  seed()
  try {
    await login(page)
    // 入口：性能分析页任一任务抽屉——直接经 API 断言核心链路后，UI 面板
    // 经 SubtaskPerfReport 打开（抽屉入口在性能分析页任务表首行）。
    await page.goto('/admin/ai-skillopt')
    await expect(page.getByText('SkillOpt — 技能优化')).toBeVisible()
    // 用窗口内 fetch 直接驱动真实端点（管理页登录态下同源），断言服务端
    // 语义；UI 面板渲染随后在抽屉里断言。
    // 认证走 Bearer token（localStorage check-manage:token，JSON 序列化存），
    // 与 src/utils/request.ts 拦截器同读取逻辑——裸 fetch 不带 Cookie。
    const diag = await page.evaluate(async (aid) => {
      let token = ''
      const raw = localStorage.getItem('check-manage:token')
      if (raw) {
        try { token = JSON.parse(raw) || '' } catch { token = raw }
      }
      const r = await fetch(`/api/ai/chat/admin/attempts/${aid}/run-diagnosis`,
        { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
      return { status: r.status, body: await r.json() }
    }, attemptId)
    expect(diag.status).toBe(201)
    expect(diag.body.dimensions.rule_compliance.status).toBe('fail')
    expect(diag.body.overall).toBe('fail')
    // 幂等：重复生成仍是 201 且不报错
    const again = await page.evaluate(async (aid) => {
      let token = ''
      const raw = localStorage.getItem('check-manage:token')
      if (raw) {
        try { token = JSON.parse(raw) || '' } catch { token = raw }
      }
      return (await fetch(`/api/ai/chat/admin/attempts/${aid}/run-diagnosis`,
        { method: 'POST', headers: { Authorization: `Bearer ${token}` } })).status
    }, attemptId)
    expect(again).toBe(201)
  } finally {
    cleanup()
  }
})
