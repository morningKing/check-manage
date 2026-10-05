/**
 * SkillOpt 定义版本归档链路（真实后端，不烧 LLM）。
 * spec: docs/superpowers/specs/2026-10-05-skill-agent-version-archive-design.md §6/§8
 * 约定: 等真实元素,绝不等 networkidle;种子行 finally 清理（db_exec 桥）。
 * 种子: 同一定义两个已归档版本 + 一个未归档版本（UUID 后缀隔离）。
 */
import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import crypto from 'node:crypto'

// package.json 带 "type": "module"，本仓库 e2e 规约以 import.meta.url 求模块目录
// （同 e2e/ai-full/batch/toolbox.ts），不直接用 __dirname。
const DIRNAME = path.dirname(fileURLToPath(import.meta.url))

const V1 = 'v1 正文'
const V2 = 'v1 正文\n+v2 新增行'
const KIND = 'skill'
const NAME = `e2e-arch-${Date.now()}`

function db(sql: string): string {
  const script = path.join(DIRNAME, 'batch', 'db_exec.py')
  return execFileSync('python', [script], { input: sql, encoding: 'utf-8' })
}

const sha = (s: string) => crypto.createHash('sha256').update(s).digest('hex')

function seed(): { v1: string; v2: string; v0: string } {
  // PG 标准字符串可跨行：V2 的真实换行直接嵌入（'\n' 转义反而是反斜杠+n）。
  // first_seen_at 用错开的时点：同一事务里 NOW() 三行相同 → 后端
  // ORDER BY first_seen_at 平局序不确定，时间线新旧序会抖动。
  db(`
    INSERT INTO ai_skill_def_versions (id, def_kind, def_name, content_hash,
      version_label, content, content_captured_at, first_seen_at)
    VALUES ('defv_${NAME}_1', '${KIND}', '${NAME}', '${sha(V1)}', 'e2e v1', '${V1}', NOW(), NOW() - INTERVAL '2 hours'),
           ('defv_${NAME}_2', '${KIND}', '${NAME}', '${sha(V2)}', 'e2e v2', '${V2}', NOW(), NOW() - INTERVAL '1 hour'),
           ('defv_${NAME}_0', '${KIND}', '${NAME}', '${'a'.repeat(64)}', NULL, NULL, NULL, NOW() - INTERVAL '3 hours')
    ON CONFLICT (def_kind, def_name, content_hash) DO NOTHING;`)
  return { v1: `defv_${NAME}_1`, v2: `defv_${NAME}_2`, v0: `defv_${NAME}_0` }
}

function cleanup(): void {
  db(`DELETE FROM ai_skill_def_versions WHERE def_kind='${KIND}' AND def_name='${NAME}';`)
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
  } catch {
    // 已是登录态——直接继续
  }
}

test('版本时间线：内容预览/相邻对比/未归档置灰', async ({ page }) => {
  test.setTimeout(180_000)
  const ids = seed()
  try {
    await login(page)
    await page.goto('/admin/ai-skillopt')
    await expect(page.getByText('SkillOpt — 技能优化')).toBeVisible()
    // 左栏选中目标定义
    const defItem = page.locator('.fit-def', { hasText: NAME }).first()
    await defItem.waitFor({ state: 'visible', timeout: 30_000 })
    await defItem.click()
    // 版本演进子标签
    await page.getByRole('tab', { name: '版本演进' }).click()
    const timeline = page.locator('.vt')
    await expect(timeline).toBeVisible()
    // 两个已归档版本的标注可见。标注经 ElInput 渲染为行内输入框的值
    // （非文本节点，getByText 匹配不到），按时间线新→旧序断言输入框值。
    const labelInputs = timeline.getByRole('textbox', { name: '版本标注，回车保存' })
    await expect(labelInputs).toHaveCount(3)
    await expect(labelInputs.nth(0)).toHaveValue('e2e v2')
    await expect(labelInputs.nth(1)).toHaveValue('e2e v1')
    // 未归档版本行内动作置灰（disabled）
    const disabledRollback = timeline.locator('button:has-text("回滚")')
      .and(page.locator('[disabled]'))
    await expect(disabledRollback.first()).toBeVisible()
    // 内容预览：新版本节点
    const newest = timeline.locator('.el-timeline-item').first()
    await newest.getByRole('button', { name: '内容' }).click()
    const contentDialog = page.locator('.el-dialog', { hasText: '版本内容' })
    await expect(contentDialog).toBeVisible()
    await expect(contentDialog.locator('pre')).toContainText('+v2 新增行')
    await contentDialog.locator('.el-dialog__headerbtn').click()
    // 相邻对比：新版本节点「对比」（from=上一版 → to=本版）
    await newest.getByRole('button', { name: '对比' }).click()
    const diffDialog = page.locator('.el-dialog', { hasText: '版本对比' })
    await expect(diffDialog).toBeVisible()
    await expect(diffDialog.locator('pre')).toContainText('+v2 新增行')
    await diffDialog.locator('.el-dialog__headerbtn').click()
    void ids
  } finally {
    cleanup()
  }
})
