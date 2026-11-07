/**
 * 运行时技能文件树 E2E（2026-10-15 文件编辑改造）：真页面 + 真全局技能目录。
 * skill-creator 是多目录技能（scripts/ + __pycache__/），验证：
 * 默认只展开根层 → 文件夹点击展开 → 搜索过滤+自动展开 → 展开全部。
 * 平台技能面板共用同一组件（本机平台技能库为空，其接入由组件测试覆盖）。
 */
import { test, expect } from '@playwright/test'
import { gotoWithAuth, screenshot } from './helpers'
import * as fs from 'node:fs'
import * as path from 'node:path'
import * as os from 'node:os'

// 多目录技能 fixture：skill-creator 原是 2026-10-26 self-evolution 期装到本机
// OC 全局目录的技能，后随机器清理消失——本测试改为自给自足（开头种、结束
// 清），不再依赖机器状态；OC 全局目录取 homedir 缺省（本测试本就测真目录）。
const OC_SKILL_ROOT = path.join(os.homedir(), '.config', 'opencode', 'skill')
const FIXTURE = path.join(OC_SKILL_ROOT, 'skill-creator')

function seedSkillFixture(): void {
  fs.mkdirSync(path.join(FIXTURE, 'scripts'), { recursive: true })
  fs.mkdirSync(path.join(FIXTURE, '__pycache__'), { recursive: true })
  fs.writeFileSync(
    path.join(FIXTURE, 'SKILL.md'),
    '---\nname: skill-creator\ndescription: create skills\n---\n\nCreate new skills.\n',
  )
  fs.writeFileSync(path.join(FIXTURE, 'license.txt'), 'MIT License\n')
  fs.writeFileSync(
    path.join(FIXTURE, 'scripts', 'init_skill.py'),
    '# Placeholder init script for runtime skill files tree E2E fixture.\n',
  )
  fs.writeFileSync(
    path.join(FIXTURE, 'scripts', 'quick_validate.py'),
    '# Placeholder validate script for runtime skill files tree E2E fixture.\n',
  )
  fs.writeFileSync(
    path.join(FIXTURE, '__pycache__', 'init.cpython-311.pyc'),
    'placeholder pyc\n',
  )
}

function removeSkillFixture(): void {
  fs.rmSync(FIXTURE, { recursive: true, force: true })
}

test('运行时技能文件：默认收起 / 文件夹展开 / 搜索过滤 / 展开全部', async ({ page }) => {
  test.setTimeout(120_000)
  seedSkillFixture()
  try {
    await runScenario(page)
  } finally {
    removeSkillFixture()
  }
})

async function runScenario(page: import('@playwright/test').Page): Promise<void> {
  await gotoWithAuth(page, '/admin/ai-settings?tab=runtime')

  // 技能列表分页（每页 10 条）→ 用页面检索框过滤到多目录技能 skill-creator
  const search = page.locator('[data-test="skill-search"]')
  await expect(search).toBeVisible({ timeout: 30_000 })
  await search.fill('skill-creator')
  const row = page.locator('tr', { hasText: 'skill-creator' }).first()
  await expect(row).toBeVisible({ timeout: 15_000 })
  await row.getByRole('button', { name: '文件' }).click()
  const dialog = page.locator('.el-dialog').filter({ hasText: '技能文件 — skill-creator' })
  await expect(dialog).toBeVisible({ timeout: 15_000 })

  // 默认只展开根层：根级文件与顶层文件夹可见，子文件未展开
  await expect(dialog.locator('[data-path="SKILL.md"]')).toBeVisible()
  await expect(dialog.locator('[data-path="license.txt"]')).toBeVisible()
  await expect(dialog.locator('[data-dir="scripts"]')).toBeVisible()
  await expect(dialog.locator('[data-path="scripts/init_skill.py"]')).toHaveCount(0)
  await screenshot(page, 'runtime-skill-files-collapsed')

  // 点击文件夹逐层展开
  await dialog.locator('[data-dir="scripts"]').click()
  await expect(dialog.locator('[data-path="scripts/init_skill.py"]')).toBeVisible()

  // 搜索：命中深层文件并自动展开祖先，未命中行消失
  await dialog.locator('[data-test="file-search"]').fill('quick_validate')
  await expect(dialog.locator('[data-path="scripts/quick_validate.py"]')).toBeVisible()
  await expect(dialog.locator('[data-path="SKILL.md"]')).toHaveCount(0)

  // 清空搜索 + 展开全部：全部文件可见
  await dialog.locator('[data-test="file-search"]').fill('')
  await dialog.locator('[data-test="expand-all"]').click()
  await expect(dialog.locator('[data-path="license.txt"]')).toBeVisible()
  await expect(dialog.locator('[data-dir="__pycache__"]')).toBeVisible()
  await screenshot(page, 'runtime-skill-files-expanded')

  // 折叠全部：回到根层
  await dialog.locator('[data-test="collapse-all"]').click()
  await expect(dialog.locator('[data-path="scripts/init_skill.py"]')).toHaveCount(0)
  await expect(dialog.locator('[data-path="SKILL.md"]')).toBeVisible()
}
