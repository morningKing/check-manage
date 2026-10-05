/**
 * ai-full 批任务 UI 流程唯一实现（2026-10-04 从旧 spec 的复制粘贴段收敛）。
 *
 * 来源（选择器逐字保留，已对线上 UI 验证，勿重写）：
 * - 对话框流程：e2e/ai-chat-batch.spec.ts 的「侧栏批任务分组头 新建 →
 *   dialog → data-test=name/prompt → setInputFiles → create-btn」段；
 * - 展开重试循环：e2e/ai-chat-stop-resume.spec.ts 的 expandBatchGroup
 *   （与 ai-chat-batch.spec.ts 内联版同构；取参数化 + 组等待 15s 的这份）；
 * - 状态徽标读取：两份 spec 的 page.waitForFunction 徽标判定段
 *   （.batch-group → .bg-name 按名匹配 → .badge 的 badge--xxx 修饰类）。
 *
 * 仅做参数化（批次名/prompt/文件内容提升为参数），不泛化：
 * - 上传一律 mimeType 'text/plain' 内存 buffer（与源码一致）；
 * - 子任务数量断言（.bg-child toHaveCount(n)）取决于调用方上传的文件数，
 *   留在调用方；
 * - 登录、goto('/ai-chat')、批组终态轮询也留在调用方（各场景超时预算不同）。
 */
import { expect, type Page } from '@playwright/test'

/** 打开侧栏「批任务→新建」对话框并阻塞到对话框可见。 */
export async function openBatchDialog(page: Page): Promise<void> {
  // 侧栏分组头是 .ai-sidebar__section-head（会话/批任务/AI定时任务 三个），
  // 用「批任务」文本过滤出目标分组的「新建」按钮。
  const createBatchBtn = page
    .locator('.ai-sidebar__section-head', { hasText: '批任务' })
    .locator('button', { hasText: '新建' })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 15_000 })
  await createBatchBtn.click()

  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.waitFor({ state: 'visible', timeout: 15_000 })
}

/** 在已打开的「新建批任务」对话框里填写基础字段并提交：
 * name/prompt 填入 data-test 输入框；files（可选）经对话框 ElUpload 的
 * input[type=file] 以内存 buffer 上传，并逐个等待出现在对话框自己的
 * .files 暂存列表。返回前不等待批组出现——后续展开用 expandBatchGroup
 * （自带组可见等待）。 */
export async function createBatchViaDialog(page: Page, o: {
  name: string; prompt: string; files?: { name: string; content: string }[]
}): Promise<void> {
  // 一律把后续操作 scope 到对话框 —— 页面上还有别的 file input（聊天
  // 输入框），且历史批组子任务可能与新上传文件同名。
  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.locator('input[data-test="name"]').fill(o.name)
  await dialog.locator('textarea[data-test="prompt"]').fill(o.prompt)

  if (o.files?.length) {
    await dialog.locator('input[type="file"]').setInputFiles(
      o.files.map(f => ({
        name: f.name, mimeType: 'text/plain', buffer: Buffer.from(f.content, 'utf-8'),
      })),
    )
    for (const f of o.files) {
      await expect(dialog.locator('.files')).toContainText(f.name, { timeout: 8_000 })
    }
  }

  const createBtn = dialog.locator('button[data-test="create-btn"]')
  await expect(createBtn).toBeEnabled({ timeout: 8_000 })
  await createBtn.click()
}

/** 展开指定名称的批组（带既有重试循环语义）：详情 5s 轮询会重渲染列表、
 * 可能吞掉展开点击 —— 点击组头重试直到 .batch-group__body 出现（5 次 ×
 * 500ms）；仍未展开则抛错（源码里这一失败由后续 toHaveCount 兜住，此处
 * 显式化）。子任务数量断言由调用方自行执行。 */
export async function expandBatchGroup(page: Page, name: string): Promise<void> {
  const group = page.locator('.batch-group', { hasText: name }).first()
  await group.waitFor({ state: 'visible', timeout: 15_000 })
  const head = group.locator('.batch-group__head')
  for (let i = 0; i < 5 && (await group.locator('.batch-group__body').count()) === 0; i++) {
    await head.click()
    await page.waitForTimeout(500)
  }
  if ((await group.locator('.batch-group__body').count()) === 0) {
    throw new Error(`批组「${name}」重试 5 次后仍未展开（.batch-group__body 未出现）`)
  }
}

/** 读取指定名称批组的状态徽标修饰类（badge--running / badge--paused /
 * badge--completed 等）。组或徽标不存在 → 等待超时抛错；徽标在但尚无
 * badge-- 修饰类 → 返回空串（由调用方轮询重试）。 */
export async function batchGroupBadgeClass(page: Page, name: string): Promise<string> {
  // 与源码 waitForFunction 同语义：按 .bg-name 文本匹配组（而非整组
  // hasText，避免子任务文本撞名）。
  const group = page.locator('.batch-group', {
    has: page.locator('.bg-name', { hasText: name }),
  }).first()
  const classAttr = await group.locator('.badge').getAttribute('class', { timeout: 15_000 })
  return (classAttr ?? '').split(/\s+/).find(c => c.startsWith('badge--')) ?? ''
}
