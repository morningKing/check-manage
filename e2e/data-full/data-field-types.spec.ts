/**
 * 族B L3 字段类型 UI 旅程（TD-B15–B20，共 6 例）。
 *
 * 选择器锚点全部按组件实读落定（2026-10-06 实读，file:line 见 task-4-report）：
 * - AutoTimestamp.vue:9     只读 span.auto-timestamp（无值「自动生成」）——FormRenderer.vue:106-110
 *                           把 autoTimestamp 字段过滤出表单，故 B15 只经 store 填充，UI 无输入框。
 * - AutoSequence.vue:9      只读 span.auto-sequence（无值「保存后生成」）。
 * - CompositeText.vue:2     只读 span.composite-text（formData 实时预览）；store 在
 *                           add/updatePageData（pageConfig.ts:526-534/597-605）落库时计算。
 * - MarkdownEditor.vue:9    md-editor-v3 v6（CodeMirror 6）：可编辑区为
 *                           .markdown-editor .cm-content[contenteditable]（无 closeBrackets，
 *                           md-editor-v3/lib/es/MdEditor.mjs:9-13 未引入自动配对）。
 * - MarkdownPreview.vue:17  详情弹窗用统一 MarkdownPreview（DynamicPage.vue:569-576），渲染 h1/strong。
 * - FileUpload.vue:11-23    el-upload 真后端上传（POST /api/data-files/upload），原生
 *                           input[type=file] 可直接 setInputFiles；成功回调 ElMessage「上传成功」。
 * - WorkflowActions.vue:2-13 仅渲染于查看记录弹窗 footer（DynamicPage.vue:593-600，guest 不渲染），
 *                           点击后打开「推进意见」对话框（DynamicPage.vue:621-649，append-to-body）。
 *
 * 已知产品缺陷（不修产品，测试按真实行为断言）：FileUpload 移除路径失效——
 * watch（FileUpload.vue:104-119）把列表项 uid 重映射为 index，而 handleRemove
 * （FileUpload.vue:192-196）按 f.uid !== String(file.uid) 过滤（uid 为 data_files
 * 的 uuid），永不匹配，删除项经 watch 重渲染复活。故 B18「替换」经真实 UI 只能
 * 达成「追加」，断言按追加语义写。
 */
import { test, expect } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'
import {
  createDataPage, deleteDataPage, gotoWithAuth, listRecords,
  screenshot, type DataPageHandle, type FieldLite,
} from './helpers'

test.setTimeout(120_000)

/** 各用例自建数据页，afterAll 统一回收（workers=1 串行，无并发撞名）。 */
const pages: DataPageHandle[] = []

async function makePage(request: APIRequestContext, purpose: string,
                        fields: FieldLite[]): Promise<DataPageHandle> {
  const h = await createDataPage(request, 'B', purpose, fields)
  pages.push(h)
  return h
}

test.afterAll(async ({ request }) => {
  for (const h of pages) await deleteDataPage(request, h)
})

const NAME_FIELD: FieldLite = {
  id: 'f1', label: '名称', fieldName: 'name', controlType: 'text',
  required: true, order: 1, placeholder: '请输入名称',
}

// ---------------------------------------------------------------------------
// TD-B15 autoTimestamp：store 填充（create）+ 刷新（update）
// ---------------------------------------------------------------------------
test('TD-B15 autoTimestamp UI 提交后填充并在编辑后刷新', async ({ page, request }) => {
  const h = await makePage(request, 'ts', [
    NAME_FIELD,
    { id: 'f2', label: '时间戳', fieldName: 'ts', controlType: 'autoTimestamp',
      required: false, order: 2 },
  ])
  await gotoWithAuth(page, h.path)

  // 新增：表单被 FormRenderer 过滤后只剩 name，ts 由 store addPageData 填充
  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('请输入名称').fill('时戳甲')
  await dialog.getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: '时戳甲' }))
    .toBeVisible()

  const first = await listRecords(request, h.collection)
  const rec1 = first.json.data.find((r: any) => r.name === '时戳甲')
  expect(rec1, '创建的记录应存在').toBeTruthy()
  expect(String(rec1.ts), 'ts 应为前端填充的 ISO 时间戳').toMatch(/^\d{4}-\d{2}-\d{2}T/)
  const ts1: string = rec1.ts

  // 时钟前进后经 UI 编辑 → updatePageData 刷新 ts
  await page.waitForTimeout(1200)
  const row = page.locator('.table-card .el-table__body tr', { hasText: '时戳甲' })
  await row.getByRole('button', { name: '编辑' }).click()
  const editDialog = page.locator('.el-dialog:visible')
  await editDialog.getByPlaceholder('请输入名称').fill('时戳甲-改')
  await editDialog.getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: '时戳甲-改' }))
    .toBeVisible()

  const second = await listRecords(request, h.collection)
  const rec2 = second.json.data.find((r: any) => r.name === '时戳甲-改')
  expect(String(rec2.ts), '编辑后 ts 仍应为 ISO 格式').toMatch(/^\d{4}-\d{2}-\d{2}T/)
  expect(rec2.ts !== ts1, `编辑后 ts 应刷新（${ts1} → ${rec2.ts}）`).toBe(true)
})

// ---------------------------------------------------------------------------
// TD-B16 compositeText：store 计算 + 编辑重算
// ---------------------------------------------------------------------------
test('TD-B16 compositeText UI 提交计算并在编辑后重算', async ({ page, request }) => {
  const h = await makePage(request, 'comp', [
    NAME_FIELD,
    { id: 'f2', label: '数量', fieldName: 'qty', controlType: 'number',
      required: false, order: 2, placeholder: '请输入数量' },
    { id: 'f3', label: '组合', fieldName: 'comp', controlType: 'compositeText',
      required: false, order: 3,
      compositeTextConfig: { sourceFields: ['name', 'qty'], separator: ' - ' } },
  ])
  await gotoWithAuth(page, h.path)

  // 新增：表单内 composite-text span 实时预览（CompositeText.vue:17-28）
  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('请输入名称').fill('组合甲')
  await dialog.getByPlaceholder('请输入数量').fill('3')
  await expect(dialog.locator('.el-form-item', { hasText: '组合' })
    .locator('.composite-text')).toHaveText('组合甲 - 3')
  await dialog.getByRole('button', { name: '确定' }).click()

  const row = page.locator('.table-card .el-table__body tr', { hasText: '组合甲' })
  await expect(row).toBeVisible()
  await expect(row).toContainText('组合甲 - 3')

  // 编辑 name → updatePageData 重算 comp
  await row.getByRole('button', { name: '编辑' }).click()
  const editDialog = page.locator('.el-dialog:visible')
  await editDialog.getByPlaceholder('请输入名称').fill('组合乙')
  await editDialog.getByRole('button', { name: '确定' }).click()

  const row2 = page.locator('.table-card .el-table__body tr', { hasText: '组合乙' })
  await expect(row2).toBeVisible()
  await expect(row2).toContainText('组合乙 - 3')

  const listed = await listRecords(request, h.collection)
  const rec = listed.json.data.find((r: any) => r.name === '组合乙')
  expect(rec?.comp, 'comp 应落库为重算值').toBe('组合乙 - 3')
})

// ---------------------------------------------------------------------------
// TD-B17 markdown：MdEditor 编辑 → 表格纯文本摘要 → 详情弹窗 h1/strong 渲染
// ---------------------------------------------------------------------------
test('TD-B17 markdown 编辑、表格摘要与详情渲染', async ({ page, request }) => {
  const h = await makePage(request, 'md', [
    NAME_FIELD,
    { id: 'f2', label: '文档', fieldName: 'md', controlType: 'markdown',
      required: false, order: 2 },
  ])
  await gotoWithAuth(page, h.path)

  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('请输入名称').fill('MD甲')
  // md-editor-v3 v6 = CodeMirror 6：可编辑区为 .cm-content[contenteditable]
  const cm = dialog.locator('.markdown-editor .cm-content')
  await expect(cm).toBeVisible()
  await cm.click()
  await page.keyboard.type('# 标题甲')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Enter')
  await page.keyboard.type('**粗体**文本')
  await dialog.getByRole('button', { name: '确定' }).click()

  // 表格单元格 = 去 Markdown 标记的纯文本摘要（DataTable.vue:583-592）
  const row = page.locator('.table-card .el-table__body tr', { hasText: 'MD甲' })
  await expect(row).toBeVisible()
  await expect(row).toContainText('标题甲 粗体文本')
  await expect(row).not.toContainText('#')
  await expect(row).not.toContainText('**')

  // 详情弹窗：MarkdownPreview 渲染 h1/strong（DynamicPage.vue:569-576）
  await row.locator('.row-actions-trigger').click()
  await page.locator('.el-dropdown-menu__item', { hasText: '查看' }).click()
  const viewer = page.locator('.el-dialog:visible', { hasText: '查看记录' })
  await expect(viewer).toBeVisible()
  await expect(viewer.locator('.view-markdown h1')).toHaveText('标题甲')
  await expect(viewer.locator('.view-markdown strong')).toHaveText('粗体')
  await screenshot(page, 'ft-md-render')
  // 头部 X aria-label=「关闭此对话框」，exact 只匹配底部「关闭」
  await viewer.getByRole('button', { name: '关闭', exact: true }).click()
  await expect(viewer).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// TD-B18 file：真后端上传 + 记录落库为数组；编辑追加第二个文件
// ---------------------------------------------------------------------------
test('TD-B18 文件上传 UI 落库为数组并经编辑追加', async ({ page, request }) => {
  const h = await makePage(request, 'file', [
    NAME_FIELD,
    { id: 'f2', label: '附件', fieldName: 'attach', controlType: 'file',
      required: false, order: 2 },
  ])
  await gotoWithAuth(page, h.path)

  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('请输入名称').fill('附件甲')
  // el-upload 原生 input 可直接 setInputFiles（FileUpload.vue:11 自定义 http-request）
  await dialog.locator('input[type="file"]').setInputFiles({
    name: 'dtest-甲.txt', mimeType: 'text/plain',
    buffer: Buffer.from('DTEST 文件内容 甲', 'utf-8'),
  })
  // 上传成功回调 ElMessage.success('上传成功')（FileUpload.vue:185-187）
  await expect(page.locator('.el-message', { hasText: '上传成功' }))
    .toBeVisible({ timeout: 15_000 })
  await dialog.getByRole('button', { name: '确定' }).click()

  const row = page.locator('.table-card .el-table__body tr', { hasText: '附件甲' })
  await expect(row).toBeVisible()
  // 表格 file 单元格 = `${length} 个文件`（DataTable.vue:554-559）
  await expect(row).toContainText('1 个文件')
  await screenshot(page, 'ft-file-uploaded')

  let listed = await listRecords(request, h.collection)
  let rec = listed.json.data.find((r: any) => r.name === '附件甲')
  expect(Array.isArray(rec?.attach), 'attach 应为数组').toBe(true)
  expect(rec.attach, '应恰好 1 个文件').toHaveLength(1)
  expect(rec.attach[0].name).toBe('dtest-甲.txt')
  expect(String(rec.attach[0].uid), 'uid 应为 data_files.id（uuid）')
    .toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
  expect(rec.attach[0].url).toMatch(/^\/api\/data-files\/[0-9a-f-]+\/download$/)

  // 编辑追加第二个文件（「替换」经真实 UI 不可达：移除路径缺陷，见文件头注释）
  await row.getByRole('button', { name: '编辑' }).click()
  const editDialog = page.locator('.el-dialog:visible')
  await editDialog.locator('input[type="file"]').setInputFiles({
    name: 'dtest-乙.txt', mimeType: 'text/plain',
    buffer: Buffer.from('DTEST 文件内容 乙', 'utf-8'),
  })
  await expect(page.locator('.el-message', { hasText: '上传成功' }))
    .toBeVisible({ timeout: 15_000 })
  await editDialog.getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: '附件甲' }))
    .toContainText('2 个文件')

  listed = await listRecords(request, h.collection)
  rec = listed.json.data.find((r: any) => r.name === '附件甲')
  expect(rec.attach, '第二次上传后应为 2 个文件').toHaveLength(2)
  expect(rec.attach.map((f: any) => f.name))
    .toEqual(['dtest-甲.txt', 'dtest-乙.txt'])
})

// ---------------------------------------------------------------------------
// TD-B19 autoSequence：表单只读占位 + 服务端原子分配 DTSU-001/002
// ---------------------------------------------------------------------------
test('TD-B19 autoSequence 表单只读并生成递增序号', async ({ page, request }) => {
  const h = await makePage(request, 'seq', [
    NAME_FIELD,
    { id: 'f2', label: '编号', fieldName: 'sn', controlType: 'autoSequence',
      required: false, order: 2, sequenceConfig: { prefix: 'DTSU-', max: 999 } },
  ])
  await gotoWithAuth(page, h.path)

  // 表单中 sn 为只读 span，无值显示「保存后生成」（AutoSequence.vue:9-26）
  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await expect(dialog.locator('.el-form-item', { hasText: '编号' })
    .locator('.auto-sequence')).toHaveText('保存后生成')
  await dialog.getByPlaceholder('请输入名称').fill('序号甲')
  await dialog.getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: '序号甲' }))
    .toContainText('DTSU-001')

  // 第二条 → DTSU-002（服务端 allocate_sequence，dynamic.py:653-660）
  await page.getByRole('button', { name: '新增' }).click()
  const dialog2 = page.locator('.el-dialog:visible')
  await expect(dialog2.locator('.el-form-item', { hasText: '编号' })
    .locator('.auto-sequence')).toHaveText('保存后生成')
  await dialog2.getByPlaceholder('请输入名称').fill('序号乙')
  await dialog2.getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: '序号乙' }))
    .toContainText('DTSU-002')

  const listed = await listRecords(request, h.collection)
  const sns = listed.json.data
    .map((r: any) => r.sn).sort()
  expect(sns).toEqual(['DTSU-001', 'DTSU-002'])
})

// ---------------------------------------------------------------------------
// TD-B20 字段级 workflow：查看弹窗 footer 按钮 → 推进意见 → 确认 → 状态流转
// ---------------------------------------------------------------------------
test('TD-B20 workflow 字段级流转 UI', async ({ page, request }) => {
  const h = await makePage(request, 'wf', [
    NAME_FIELD,
    { id: 'f2', label: '状态', fieldName: 'status', controlType: 'select',
      required: false, order: 2, placeholder: '请选择状态',
      options: [
        { label: '待处理', value: 'todo' },
        { label: '进行中', value: 'doing' },
        { label: '已完成', value: 'done' },
      ],
      workflowConfig: {
        enabled: true,
        transitions: [
          { from: 'todo', to: 'doing', label: '开始', roles: [] },
          { from: 'doing', to: 'done', label: '完成', roles: [] },
        ],
      } },
  ])
  await gotoWithAuth(page, h.path)

  // 新增 status=todo（首写不校验流转，dynamic.py:844 old_val is not None 才校验）
  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('请输入名称').fill('流转甲')
  // el-select placeholder 渲染为 span，用表单项 label 锚点（计划①同款）
  await dialog.locator('.el-form-item', { hasText: '状态' })
    .locator('.el-select__wrapper').click()
  await page.locator('.el-select-dropdown:visible .el-select-dropdown__item',
                     { hasText: '待处理' }).click()
  await dialog.getByRole('button', { name: '确定' }).click()
  const row = page.locator('.table-card .el-table__body tr', { hasText: '流转甲' })
  await expect(row).toBeVisible()

  // 打开查看弹窗（行「更多」下拉 → 查看），footer 出现「开始」按钮
  await row.locator('.row-actions-trigger').click()
  await page.locator('.el-dropdown-menu__item', { hasText: '查看' }).click()
  const viewer = page.locator('.el-dialog:visible', { hasText: '查看记录' })
  await expect(viewer).toBeVisible()
  const startBtn = viewer.locator('.workflow-actions')
    .getByRole('button', { name: '开始' })
  await expect(startBtn, 'WorkflowActions 应在查看弹窗 footer 渲染「开始」')
    .toBeVisible({ timeout: 10_000 })

  // 点击「开始」→「推进意见」对话框（append-to-body）→ 填意见 → 确认
  await startBtn.click()
  const commentBox = page.locator('.el-dialog:visible', { hasText: '推进意见' })
  await expect(commentBox).toBeVisible({ timeout: 10_000 })
  await commentBox.locator('textarea').fill('同意推进')
  await commentBox.getByRole('button', { name: '确认' }).click()
  await expect(commentBox).not.toBeVisible({ timeout: 10_000 })

  const listed = await listRecords(request, h.collection)
  const rec = listed.json.data.find((r: any) => r.name === '流转甲')
  expect(rec?.status, '流转后 status 应为 doing').toBe('doing')
  await screenshot(page, 'ft-wf-transitioned')

  // 关闭后重开查看弹窗 → 出现下一流转「完成」按钮
  await viewer.getByRole('button', { name: '关闭', exact: true }).click()
  await expect(viewer).not.toBeVisible()
  await page.locator('.table-card .el-table__body tr', { hasText: '流转甲' })
    .locator('.row-actions-trigger').click()
  await page.locator('.el-dropdown-menu__item', { hasText: '查看' }).click()
  const viewer2 = page.locator('.el-dialog:visible', { hasText: '查看记录' })
  await expect(viewer2).toBeVisible()
  await expect(viewer2.locator('.workflow-actions')
    .getByRole('button', { name: '完成' })).toBeVisible({ timeout: 10_000 })
  await viewer2.getByRole('button', { name: '关闭', exact: true }).click()
})
