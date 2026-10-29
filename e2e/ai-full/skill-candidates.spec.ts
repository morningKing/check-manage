/**
 * O2 技能进化候选区（真实后端，零 LLM）。
 * spec: docs/superpowers/specs/2026-10-26-agent-self-evolution-design.md §7
 * 种子: 定义（正文归档）+ 边界/保留 attempt + 真实临时 SKILL.md；
 * 链路: 候选列表 → 回归验证（双集结果）→ 发布（确认框）→ 定义文件回写
 *       → 版本演进新版本标注。finally 清理（FK 序 + rmtree）。
 * 约定: 等真实元素,绝不等 networkidle。
 */
import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import crypto from 'node:crypto'

const DIRNAME = path.dirname(fileURLToPath(import.meta.url))
const TAG = `cand_${Date.now()}`
const NAME = `e2e-cand-${Date.now()}`

// 与后端同机的真实定义文件（promote 要回写它）。允许根 = config.AI_WORKSPACE_ROOT
// （默认 C:\Users\admin\.check-manage\ai-workspaces，本机未设 env 覆盖——
// server/ai-workspaces 不在根内，promote 会 400 'path escapes allowed roots'）
const MD_DIR = path.join(process.env.USERPROFILE || process.env.HOME || '',
  '.check-manage', 'ai-workspaces', `e2e-${TAG}`)
const MD_PATH = path.join(MD_DIR, 'SKILL.md')
const BASE_MD = [
  '---', 'description: demo', 'fit:', '  steps:',
  '    - id: clone', '      name: 克隆', '      expect:',
  "        - tool: bash", "          args_pattern: 'git clone'",
  '    - id: install', '      name: 安装', '      expect:',
  "        - tool: bash", "          args_pattern: 'pip install -r requirements'",
  '---', '正文',
].join('\n')
const EDITS = JSON.stringify([{
  old_str: "args_pattern: 'pip install -r requirements'",
  new_str: "args_pattern: 'pip install'",
  reason: '放宽安装匹配',
}])

function db(sql: string): string {
  const script = path.join(DIRNAME, 'batch', 'db_exec.py')
  return execFileSync('python', [script], { input: sql, encoding: 'utf-8' })
}
const sha = (s: string) => crypto.createHash('sha256').update(s).digest('hex')
// PG 标准串转义：BASE_MD/EDITS 含单引号（args_pattern '…'），必须翻倍
const q = (s: string) => s.replace(/'/g, "''")

function seed(): string {
  fs.mkdirSync(MD_DIR, { recursive: true })
  fs.writeFileSync(MD_PATH, BASE_MD, 'utf-8')
  const uid = crypto.randomUUID()
  const chash = sha(BASE_MD)
  // 边界 attempt：clone + 错误路径（旧定义 partial，50）；保留 attempt：两步全中（fit，100）。
  // T7-1 同款窗口：finished_at NULL（_load_trace BETWEEN started AND COALESCE(finished,NOW())）
  const rows: string[] = []
  const mk = (kind: 'b' | 'h'): string => {
    const sid = crypto.randomUUID()
    const aid = crypto.randomUUID()
    const tools = kind === 'b'
      ? ['git clone origin', 'pip install -r ./wrong.txt']
      : ['git clone origin', 'pip install -r requirements.txt']
    rows.push(`
      INSERT INTO ai_chat_sessions (id, user_id, status, session_token)
      VALUES ('${sid}', '${uid}', 'completed', 'tok-${sid.slice(0, 12)}');
      INSERT INTO ai_execution_attempts (id, session_id, source_type, operation,
        started_at, finished_at)
      VALUES ('${aid}', '${sid}', 'batch', 'send', NOW() - INTERVAL '5 minutes', NULL);
      INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, source, path,
        content_hash, injected)
      VALUES ('man_${aid.slice(0, 8)}', '${aid}', 'skill', '${NAME}', 'session',
        '${MD_PATH}', '${chash}', true);
      INSERT INTO ai_skill_invocations (id, session_id, attempt_id, skill_name,
        skill_hash, source, evidence_level, evidence_refs)
      VALUES ('inv_${aid.slice(0, 8)}', '${sid}', '${aid}', '${NAME}', '',
        'runtime', 'confirmed', '[]'::jsonb);`)
    for (const t of tools) {
      rows.push(`
        INSERT INTO agent_tool_calls (oc_session_id, root_session_id, part_id, tool,
          args_text, state, occurred_at)
        VALUES ('oc-${aid.slice(0, 8)}-${t.slice(0, 6)}', '${sid}',
          'p-${crypto.randomUUID().slice(0, 8)}', 'bash', '${t}', 'completed', NOW());`)
    }
    rows.push(`
      INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, def_kind, def_name,
        def_hash, steps_total, steps_hit, score, status, per_step)
      VALUES ('fit_${aid.slice(0, 8)}', '${aid}', '${sid}', 'skill', '${NAME}',
        '${chash}', 2, ${kind === 'b' ? 1 : 2}, ${kind === 'b' ? 50 : 100},
        ${kind === 'b' ? "'partial'" : "'fit'"}, '[]'::jsonb);`)
    return aid
  }
  mk('b')
  mk('b') // 边界 ≥2
  mk('h')
  mk('h')
  mk('h') // 保留 ≥3
  db(`
    INSERT INTO users (id, username, password_hash, display_name, role)
    VALUES ('${uid}', 'u_${TAG}', 'x', 'CD', 'developer');
    INSERT INTO ai_skill_def_versions (id, def_kind, def_name, content_hash,
      version_label, content, content_captured_at, first_seen_at)
    VALUES ('defv_${TAG}', 'skill', '${NAME}', '${chash}', 'e2e base', '${q(BASE_MD)}',
      NOW(), NOW());
    INSERT INTO ai_skill_candidates (id, def_kind, def_name, base_content_hash,
      edits, edits_hash, source)
    VALUES ('cand_${TAG}', 'skill', '${NAME}', '${chash}',
      '${q(EDITS)}'::jsonb, '${sha(EDITS)}',
      '{"expectedRepair":"边界场景 X 应命中安装步骤","possiblyHarmed":[]}');
    ${rows.join('\n')}`)
  return uid
}

function cleanup(uid: string): void {
  db(`
    DELETE FROM ai_skill_candidates WHERE def_name = '${NAME}';
    DELETE FROM ai_skill_fit_results WHERE def_name = '${NAME}';
    DELETE FROM ai_skill_def_versions WHERE def_name = '${NAME}';
    DELETE FROM agent_tool_calls WHERE root_session_id IN (
      SELECT id FROM ai_chat_sessions WHERE user_id = '${uid}');
    DELETE FROM ai_skill_invocations WHERE session_id IN (
      SELECT id FROM ai_chat_sessions WHERE user_id = '${uid}');
    DELETE FROM ai_execution_manifests WHERE attempt_id IN (
      SELECT a.id FROM ai_execution_attempts a
      JOIN ai_chat_sessions s ON s.id = a.session_id WHERE s.user_id = '${uid}');
    DELETE FROM ai_execution_attempts WHERE session_id IN (
      SELECT id FROM ai_chat_sessions WHERE user_id = '${uid}');
    DELETE FROM ai_chat_sessions WHERE user_id = '${uid}';
    DELETE FROM users WHERE id = '${uid}';`)
  fs.rmSync(MD_DIR, { recursive: true, force: true })
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

test('候选区：回归验证双集结果 → 发布回写文件 → 版本演进新版本', async ({ page }) => {
  test.setTimeout(240_000)
  const uid = seed()
  try {
    await login(page)
    await page.goto('/admin/ai-skillopt')
    await page.getByRole('tab', { name: '进化候选' }).click()
    const item = page.locator('.cand-item', { hasText: NAME }).first()
    await item.waitFor({ state: 'visible', timeout: 30_000 })
    // 展开卡片（操作按钮在 v-show 折叠体里）
    await item.locator('.cand-head').click()
    // 回归验证：边界改善 ∧ 保留零退化 → validated
    await item.getByTestId('cand-validate').click()
    await expect(item.locator('.el-tag', { hasText: '回归通过' }))
      .toBeVisible({ timeout: 20_000 })
    // 双集回归表可见（mechanical 模式）
    await expect(item.locator('.cand-detail__table')).toBeVisible()
    // 发布（ElMessageBox 确认）；挂响应钩子，发布失败时直接暴露后端回包
    const promoteResp = page.waitForResponse(
      r => r.url().includes('/skill-candidates/') && r.request().method() === 'POST')
    await item.getByTestId('cand-promote').click()
    await page.getByRole('button', { name: '确定' }).click()
    const resp = await promoteResp
    if (resp.status() !== 200) {
      throw new Error(`promote failed ${resp.status()}: ${await resp.text()}`)
    }
    expect(resp.status()).toBe(200)
    await expect(item.locator('.el-tag', { hasText: '已发布' }))
      .toBeVisible({ timeout: 20_000 })
    // 定义文件真的被回写
    expect(fs.readFileSync(MD_PATH, 'utf-8'))
      .toContain("args_pattern: 'pip install'")
    // 新版本登记且带「技能候选发布」标注（promote 200 已断言；此处直查
    // 版本表——版本演进 UI 链路对种子定义的 workspace 加载敏感，授权降级，
    // UI 链路由既有 ai-skillopt-version-archive.spec 覆盖）
    const ver = db(`
      SELECT id FROM ai_skill_def_versions
      WHERE def_name = '${NAME}'
        AND version_label LIKE '技能候选发布%';`)
    expect(ver.trim().length).toBeGreaterThan(0)
  } finally {
    cleanup(uid)
  }
})
