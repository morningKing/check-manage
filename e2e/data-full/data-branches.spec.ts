/**
 * 族D L3 分支与依赖 UI 旅程 —— 真实链路（TD-D17–D19 + TD-D18b，共 4 例）。
 *
 * 选择器出处（本仓实读，2026-10-06；TD-D18b 2026-10-07 复核）：
 * - 操作菜单「版本管理/依赖管理」：DynamicPage.vue:198-203（isAdmin「数据治理」组，
 *   command=version/dependency），命令处理 :3105-3118 —— projectMenuId 存在时打开
 *   ProjectVersionManager（defaultTab versions/dependencies）。
 * - 版本抽屉：ProjectVersionManager.vue:2-9 el-dialog title=项目版本管理；
 *   header-actions「创建版本」按钮 :43-46；创建对话框 :147-171（placeholder
 *   请输入版本名称 :150、radio 分支（可编辑） :155、footer「创建」 :169）；
 *   「切换回主分支」按钮 :25-32（仅 currentBranch.branchId !== 'main' 时渲染）。
 * - 头部分支下拉：DynamicPage.vue:31-65（.title-row 内 el-tag 显示当前分支名 :25-30，
 *   .branch-switch-link 触发；「主分支」项 :43-49 非 main 时可点）。04 #4 已修复：
 *   页头「主分支」项走与抽屉同款 ElMessageBox 确认 + switch-main 接口
 *   （原 :2119-2123 TODO no-op 由 TD-D18b 锁定）。
 * - 依赖 tab 空态：ProjectDependencyManager.vue:96 el-empty「当前分支没有声明任何项目依赖」。
 *
 * 页面骨架（createDataPage）：workspace→project→data 三级菜单链，data 菜单的
 * parentId 即 DynamicPage projectMenuId computed（DynamicPage.vue:1530-1540）取到的
 * menu-proj-*，与 /project-versions 端点的 projectMenuId 同值。
 */
import { test, expect } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'
import {
  api, createDataPage, createRecord, deleteDataPage, gotoWithAuth, screenshot, tag,
  type DataPageHandle,
} from './helpers'

test.setTimeout(120_000)

interface PageUnderTest {
  h: DataPageHandle
  /** 该页上建过的分支名（API 或 UI），afterAll 据此回收分支及其分支上下文记录 */
  branchName?: string
}
const pages: PageUnderTest[] = []

async function newPage(request: APIRequestContext, purpose: string):
    Promise<{ h: DataPageHandle; entry: PageUnderTest }> {
  // 每例独立页面：分支状态 per-user 且三例共用 admin，页面级隔离避免互染
  const h = await createDataPage(request, 'D', purpose)
  const entry: PageUnderTest = { h }
  pages.push(entry)
  return { h, entry }
}

async function makeBranchViaApi(request: APIRequestContext, h: DataPageHandle,
                                 name: string): Promise<string> {
  // 契约同 server/tests/test_data_full_branches.py _make_branch：201 + id prj-ver-*
  const r = await api(request, 'POST', '/project-versions', {
    projectMenuId: h.projectMenuId, name, versionType: 'branch', createdBy: 'admin',
  })
  expect(r.status, `建分支 ${name} 失败: ${JSON.stringify(r.json)}`).toBe(201)
  return r.json.id
}

async function switchBranchViaApi(request: APIRequestContext, h: DataPageHandle,
                                  versionId: string): Promise<void> {
  const r = await api(request, 'POST', `/project-versions/${versionId}/switch`,
                      { projectMenuId: h.projectMenuId })
  expect(r.status, `切分支失败: ${JSON.stringify(r.json)}`).toBeLessThan(300)
}

/** 打开操作菜单并点指定命令项（DynamicPage.vue:171-206 el-dropdown） */
async function openMoreCommand(page: import('@playwright/test').Page, label: string):
    Promise<void> {
  await page.getByRole('button', { name: '操作' }).click()
  await page.locator('.el-dropdown-menu__item:visible', { hasText: label }).click()
}

test.afterAll(async ({ request }) => {
  // 清理次序（计划③ T1 教训：per-user 分支状态残留会让 deleteDataPage 只删得到
  // 「当前分支」的行）：
  // ① 若建过分支：切到该分支，删净分支上下文记录 → ② 切回 main（兜底） →
  // ③ DELETE 分支（级联快照） → ④ deleteDataPage（记录/config/菜单链）。
  for (const p of pages) {
    try {
      if (p.branchName) {
        const lst = await api(request, 'GET', `/project-versions/${p.h.projectMenuId}`)
        const br = (lst.json?.items || [])
          .find((it: any) => it.name === p.branchName && it.versionType === 'branch')
        if (br) {
          try { await switchBranchViaApi(request, p.h, br.id) } catch { /* 尽力而为 */ }
          const inBranch = await api(request, 'GET', `/${p.h.collection}?all=true`)
          for (const rec of inBranch.json?.data || []) {
            await api(request, 'DELETE',
                      `/${p.h.collection}/${encodeURIComponent(rec.id)}`)
          }
        }
      }
    } catch { /* 尽力而为 */ }
    try {
      await api(request, 'POST',
                `/project-versions/${p.h.projectMenuId}/switch-main`,
                { projectMenuId: p.h.projectMenuId })
    } catch { /* 同 python _back_to_main：兜底不抛 */ }
    try {
      if (p.branchName) {
        const lst2 = await api(request, 'GET', `/project-versions/${p.h.projectMenuId}`)
        const br2 = (lst2.json?.items || []).find((it: any) => it.name === p.branchName)
        if (br2) await api(request, 'DELETE', `/project-versions/${br2.id}`)
      }
    } catch { /* 尽力而为 */ }
    await deleteDataPage(request, p.h)
  }
})

test('TD-D17 版本管理抽屉与建分支', async ({ page, request }) => {
  const { h, entry } = await newPage(request, 'ui-br-drawer')
  const branchName = tag('D', 'ui-br')
  entry.branchName = branchName // 先登记再操作，失败也让 afterAll 能按名回收

  await gotoWithAuth(page, h.path)
  await expect(page.getByRole('button', { name: '新增' })).toBeVisible()

  // 操作菜单 → 「版本管理」（DynamicPage.vue:200）→ 抽屉（PVM.vue:4）
  await openMoreCommand(page, '版本管理')
  const drawer = page.locator('.el-dialog:visible', { hasText: '项目版本管理' })
  await expect(drawer).toBeVisible({ timeout: 10_000 })

  // 「创建版本」→ 内层创建对话框（append-to-body，PVM.vue:147）
  await drawer.getByRole('button', { name: '创建版本' }).click()
  const createDialog = page.locator('.el-dialog:visible', { hasText: '创建项目版本' })
  await expect(createDialog).toBeVisible()
  await createDialog.getByPlaceholder('请输入版本名称').fill(branchName)
  // 版本类型选「分支（可编辑）」（PVM.vue:155 radio value=branch）
  await createDialog.locator('.el-radio', { hasText: '分支（可编辑）' }).click()
  await createDialog.getByRole('button', { name: '创建', exact: true }).click()

  // 抽屉版本列表出现该分支（handleCreate → refreshData 重拉列表）
  await expect(drawer.locator('.el-table__body tr', { hasText: branchName }))
    .toBeVisible({ timeout: 15_000 })
  await screenshot(page, 'branch-drawer')

  // API 侧确认存在（GET /project-versions/<projectMenuId>）
  const lst = await api(request, 'GET', `/project-versions/${h.projectMenuId}`)
  expect(lst.status).toBe(200)
  const created = (lst.json?.items || []).find((it: any) => it.name === branchName)
  expect(created, 'API 版本列表应包含 UI 建的分支').toBeTruthy()
  expect(created.versionType).toBe('branch')
})

test('TD-D18 分支切换数据隔离', async ({ page, request }) => {
  const { h, entry } = await newPage(request, 'ui-br-switch')
  const branchName = tag('D', 'ui-brsw')
  const mainA = `主干甲-${h.collection}`
  const mainB = `主干乙-${h.collection}`
  const branchOnly = `分支独有-${h.collection}`

  // API 侧准备：main 两条 → 建分支 → 切到分支（admin 的 per-user 分支状态）
  await createRecord(request, h.collection, { name: mainA, qty: 1 })
  await createRecord(request, h.collection, { name: mainB, qty: 2 })
  const brId = await makeBranchViaApi(request, h, branchName)
  entry.branchName = branchName
  await switchBranchViaApi(request, h, brId)

  await gotoWithAuth(page, h.path)
  // 头部分支标签反映当前分支（DynamicPage.vue:25-30 el-tag）
  await expect(page.locator('.title-row .el-tag', { hasText: branchName }))
    .toBeVisible({ timeout: 15_000 })
  // 表格显示快照克隆的主干数据
  await expect(page.locator('.table-card .el-table__body tr', { hasText: mainA }))
    .toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.table-card .el-table__body tr', { hasText: mainB }))
    .toBeVisible()
  await screenshot(page, 'branch-switched-table')

  // API 在分支加 1 条 → UI 刷新（操作菜单「刷新」→ loadPageData）后可见
  await createRecord(request, h.collection, { name: branchOnly })
  await openMoreCommand(page, '刷新')
  await expect(page.locator('.table-card .el-table__body tr', { hasText: branchOnly }))
    .toBeVisible({ timeout: 15_000 })

  // 切回主分支走抽屉「切换回主分支」按钮（PVM.vue:25-32，仅非 main 分支时渲染）
  // → 确认框。04 #4 修复后页头下拉也可直切（TD-D18b 锁定）；本例保留抽屉路径
  // 作为第二条产品实路的回归覆盖。
  await openMoreCommand(page, '版本管理')
  const drawer = page.locator('.el-dialog:visible', { hasText: '项目版本管理' })
  await expect(drawer).toBeVisible({ timeout: 10_000 })
  await drawer.getByRole('button', { name: '切换回主分支' }).click()
  const confirmBox = page.locator('.el-message-box:visible')
  await expect(confirmBox).toBeVisible()
  await confirmBox.getByRole('button', { name: '确定' }).click()

  // 表格回到 main 数据：主干两条在、分支新增不可见（emit refresh → loadPageData）
  await expect(page.locator('.table-card .el-table__body tr', { hasText: mainA }))
    .toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.table-card .el-table__body tr', { hasText: mainB }))
    .toBeVisible()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: branchOnly }))
    .toHaveCount(0)
  // 头部标签回到 main（服务端 current-branch 对 main 返回 branchName:'main'，
  // server/routes/project_versions.py:432 —— 标签文案是 'main' 而非『主分支』）
  await expect(page.locator('.title-row .el-tag', { hasText: /^main$/ }))
    .toBeVisible({ timeout: 15_000 })
})

test('TD-D18b 页头下拉切回主分支', async ({ page, request }) => {
  const { h, entry } = await newPage(request, 'ui-br-header-main')
  const branchName = tag('D', 'ui-brhdr')
  const mainA = `主干甲-${h.collection}`
  const mainB = `主干乙-${h.collection}`
  const branchOnly = `分支独有-${h.collection}`

  // API 侧准备：main 两条 → 建分支 → 切到分支 → 分支加 1 条
  await createRecord(request, h.collection, { name: mainA, qty: 1 })
  await createRecord(request, h.collection, { name: mainB, qty: 2 })
  const brId = await makeBranchViaApi(request, h, branchName)
  entry.branchName = branchName
  await switchBranchViaApi(request, h, brId)
  await createRecord(request, h.collection, { name: branchOnly })

  await gotoWithAuth(page, h.path)
  await expect(page.locator('.title-row .el-tag', { hasText: branchName }))
    .toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.table-card .el-table__body tr', { hasText: branchOnly }))
    .toBeVisible({ timeout: 15_000 })

  // 页头下拉切回主分支：.branch-switch-link（DynamicPage.vue:38）→「主分支」项
  // （非 main 分支时可点，:disabled="!currentBranch?.branchId"）→ 确认框
  // （与抽屉 handleSwitchToMain 同款 ElMessageBox UX）→ 确定
  await page.locator('.branch-switch-link').click()
  const mainItem = page.locator(
    '.branch-dropdown-menu .el-dropdown-menu__item:visible',
    { hasText: '主分支' }
  )
  await expect(mainItem).toBeVisible({ timeout: 10_000 })
  await expect(mainItem).toBeEnabled()
  await mainItem.click()
  const confirmBox = page.locator('.el-message-box:visible', { hasText: '切换主分支' })
  await expect(confirmBox).toBeVisible({ timeout: 10_000 })
  await confirmBox.getByRole('button', { name: '确定' }).click()

  // 表格回到 main 数据：主干两条在、分支独有条不在；标签回 main
  await expect(page.locator('.table-card .el-table__body tr', { hasText: mainA }))
    .toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.table-card .el-table__body tr', { hasText: mainB }))
    .toBeVisible()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: branchOnly }))
    .toHaveCount(0)
  // 头部标签回到 main（同 TD-D18：服务端对 main 返回 branchName:'main'）
  await expect(page.locator('.title-row .el-tag', { hasText: /^main$/ }))
    .toBeVisible({ timeout: 15_000 })
})

test('TD-D19 依赖管理抽屉', async ({ page, request }) => {
  const { h } = await newPage(request, 'ui-dep-drawer')

  await gotoWithAuth(page, h.path)
  await expect(page.getByRole('button', { name: '新增' })).toBeVisible()

  // 操作菜单 → 「依赖管理」（DynamicPage.vue:201 command=dependency →
  // defaultTab=dependencies 打开同一抽屉）
  await openMoreCommand(page, '依赖管理')
  const drawer = page.locator('.el-dialog:visible', { hasText: '项目版本管理' })
  await expect(drawer).toBeVisible({ timeout: 10_000 })

  // 空态可见（ProjectDependencyManager.vue:96），无报错弹窗
  await expect(page.locator('.el-empty',
    { hasText: '当前分支没有声明任何项目依赖' })).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.el-message--error')).toHaveCount(0)
  await screenshot(page, 'dependency-drawer')

  // 关闭抽屉（el-dialog 头部 X）
  await drawer.locator('.el-dialog__headerbtn').click()
  await expect(drawer).not.toBeVisible()
})
