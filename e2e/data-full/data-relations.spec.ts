/**
 * 族C L3 关联 UI 旅程（TD-C11–C14，共 4 例）。
 *
 * 选择器锚点全部按组件实读落定（2026-10-06 实读，file:line 见 task-5-report）：
 * - RelationSelect.vue:3-15 / QuoteSelect.vue:3-15   el-select-v2 multiple filterable remote；
 *   ReferenceSelect.vue:2-13                          el-select-v2 单选 filterable remote。
 * - Element Plus 2.6.1 中 select-v2 复用 select 的 BEM 域
 *   （node_modules/.../select-v2/src/useSelect.mjs:26 useNamespace("select")）：
 *   包裹器 .el-select__wrapper（select.vue.mjs nsSelect.e("wrapper")）、过滤输入框
 *   input.el-select__input（nsSelect.e("input")，filterable 时 readonly=false）、
 *   多选标签 div.el-select__selected-item > el-tag > span.el-select__tags-text
 *   （select.vue.mjs showTagList renderList）、下拉根 .el-select-dropdown
 *   （select-dropdown.mjs ns.b("dropdown")）、选项 li.el-select-dropdown__item
 *   （option-item.vue.mjs ns.be("dropdown","item")），挂 body（teleported popper）。
 * - 远程搜索双段防抖：select-v2 remote debounce 300ms（useSelect.mjs:122）+
 *   useRemoteCollectionOptions.ts:58 自身 300ms → 键入后用 expect 轮询等待选项出现。
 * - FormRenderer.vue:24-40  每字段渲染 el-form-item :label="field.label" → 表单项
 *   label 文本锚点。
 * - 落库路径：relation 值经 PUT /relations/<c>/<r>/<f>（或 create 时 _relations，
 *   server/routes/dynamic.py:622,702-708）写入 data_relations 表；reference/quoteSelect
 *   值随记录 JSON 落库（dynamic.py:316 quoteSelect 为 JSONB 数组）。
 * - 编辑回显：loadPageData 把 GET /relations 合并回记录（stores/pageConfig.ts:458-475），
 *   RelationSelect/QuoteSelect 经 ensureSelectedLabels 用 ?ids= 回填标签
 *   （useRemoteCollectionOptions.ts:62-89）。
 * - 详情显示：查看弹窗 reference 行渲染 span.reference-link =
 *   `_ref_<field>_display || 裸 id`（DynamicPage.vue:477-484；display 键由
 *   resolveReferences 写入 pageConfig.ts:935-954 = 父记录[displayField]）；继承虚拟列
 *   `_ref_<field>_<inheritField>` 由 allExpandedFields 展开（DynamicPage.vue:1755-1780），
 *   无列视图时全部进入查看弹窗（columnView.ts:126-129 currentView 为空 → 全字段）。
 * - 关系图谱：查看弹窗 footer「关系图谱」按钮（DynamicPage.vue:607-609）→
 *   RelationGraphDialog（title 关系图谱，@opened=initGraph，RelationGraphDialog.vue:2-12），
 *   force-graph 在 .graph-container 内注入 canvas（RelationGraphDialog.vue:16,525-634）；
 *   图谱空态（仅中心节点无边）时 .graph-body v-show=!isEmpty 隐藏 → C14 先经 API 造 relation。
 */
import { test, expect } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'
import {
  api, createDataPage, createRecord, deleteDataPage, gotoWithAuth, listRecords,
  screenshot, type DataPageHandle, type FieldLite,
} from './helpers'

test.setTimeout(120_000)

const pages: DataPageHandle[] = []

const NAME_FIELD: FieldLite = {
  id: 'f1', label: '名称', fieldName: 'name', controlType: 'text',
  required: true, order: 1, placeholder: '请输入名称',
}

test.beforeAll(async ({ request }) => {
  // TD-C11/C14 拓扑：A.relation → B（B 先建，A 的 relationConfig 需要 B 的 collection 名）
  const relB = await createDataPage(request, 'C', 'relb', [NAME_FIELD])
  pages.push(relB)
  const relA = await createDataPage(request, 'C', 'rela', [
    NAME_FIELD,
    { id: 'f2', label: '关联', fieldName: 'rel', controlType: 'relation',
      required: false, order: 2,
      relationConfig: { targetCollection: relB.collection, displayField: 'name', targetField: 'rev' } },
  ])
  pages.push(relA)

  // TD-C12 拓扑：子.reference → 父（inheritFields ['name']）
  const refParent = await createDataPage(request, 'C', 'refp', [NAME_FIELD])
  pages.push(refParent)
  const refChild = await createDataPage(request, 'C', 'refc', [
    NAME_FIELD,
    { id: 'f2', label: '父记录', fieldName: 'pref', controlType: 'reference',
      required: false, order: 2,
      referenceConfig: { targetCollection: refParent.collection, displayField: 'name', inheritFields: ['name'] } },
  ])
  pages.push(refChild)

  // TD-C13 拓扑：Q（引用源）→ A.quoteSelect 多选 Q
  const quoteQ = await createDataPage(request, 'C', 'quotq', [NAME_FIELD])
  pages.push(quoteQ)
  const quoteA = await createDataPage(request, 'C', 'quota', [
    NAME_FIELD,
    { id: 'f2', label: '引用', fieldName: 'quote', controlType: 'quoteSelect',
      required: false, order: 2,
      quoteConfig: { targetCollection: quoteQ.collection, displayField: 'name' } },
  ])
  pages.push(quoteA)
})

test.afterAll(async ({ request }) => {
  for (const h of [...pages].reverse()) await deleteDataPage(request, h)
})

/** 打开行「更多」下拉 → 查看（TD-B17 同款路径）。
 *  注意：el-dropdown 的菜单逐行挂 body（每行一份），必须用 :visible 限定当前展开的菜单。 */
async function openViewer(page: import('@playwright/test').Page, rowText: string) {
  const row = page.locator('.table-card .el-table__body tr', { hasText: rowText })
  await row.locator('.row-actions-trigger').click()
  await page.locator('.el-dropdown-menu__item:visible', { hasText: '查看' }).click()
  const viewer = page.locator('.el-dialog:visible', { hasText: '查看记录' })
  await expect(viewer).toBeVisible()
  return viewer
}

// ---------------------------------------------------------------------------
// TD-C11 relation 选择器：远程搜索下拉点选 → /relations 落库 → 编辑回显
// ---------------------------------------------------------------------------
test('TD-C11 relation 选择器 UI 关联落库并回显', async ({ page, request }) => {
  const hA = pages[1] // relA
  const hB = pages[0] // relB
  const bName = `关联目标-${Date.now()}`
  const b = await createRecord(request, hB.collection, { name: bName })
  const bId = b.json.id as string
  expect(b.status, 'B 记录应创建成功').toBe(201)

  await gotoWithAuth(page, hA.path)
  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('请输入名称').fill('关联主记录甲')

  // relation 选择器（multiple + filterable + remote）：点开 → 键入 B 名称 → 下拉点选
  const relItem = dialog.locator('.el-form-item', { hasText: '关联' })
  await relItem.locator('.el-select__wrapper').click()
  await relItem.locator('input.el-select__input').fill(bName)
  const opt = page.locator('.el-select-dropdown:visible .el-select-dropdown__item',
    { hasText: bName })
  await expect(opt, '远程搜索应命中 B 记录').toBeVisible({ timeout: 10_000 })
  await opt.click()
  await screenshot(page, 'rel-picker-selected')
  await page.keyboard.press('Escape') // multiple 下拉点选后不自动收起
  await dialog.getByRole('button', { name: '确定' }).click()

  const row = page.locator('.table-card .el-table__body tr', { hasText: '关联主记录甲' })
  await expect(row).toBeVisible()

  // 落库断言：relation 不入记录 JSON，而在 data_relations 表（GET /relations/<c>/<r>）
  const listed = await listRecords(request, hA.collection)
  const rec = listed.json.data.find((r: any) => r.name === '关联主记录甲')
  expect(rec, 'A 记录应存在').toBeTruthy()
  const rels = await api(request, 'GET', `/relations/${hA.collection}/${rec.id}`)
  expect(rels.status).toBe(200)
  expect(rels.json.rel, `应关联到 ${bId}`).toEqual([bId])

  // 重开编辑对话框：已选项经 ?ids= 回填标签（bName）回显
  await row.getByRole('button', { name: '编辑' }).click()
  const editDialog = page.locator('.el-dialog:visible')
  const editRelItem = editDialog.locator('.el-form-item', { hasText: '关联' })
  await expect(editRelItem
    .locator('.el-select__selected-item .el-select__tags-text', { hasText: bName }))
    .toBeVisible({ timeout: 10_000 })
  await editDialog.getByRole('button', { name: '取消' }).click()
})

// ---------------------------------------------------------------------------
// TD-C12 reference 选择器 + 继承显示：选父 → ref 落库父 id → 详情显示父名（_ref_ 解析）
// ---------------------------------------------------------------------------
test('TD-C12 reference 选择器 UI 选父并在详情继承显示', async ({ page, request }) => {
  const hParent = pages[2] // refParent
  const hChild = pages[3] // refChild
  const parentName = `父记录-${Date.now()}`
  const p = await createRecord(request, hParent.collection, { name: parentName })
  const parentId = p.json.id as string
  expect(p.status, '父记录应创建成功').toBe(201)

  await gotoWithAuth(page, hChild.path)
  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('请输入名称').fill('子记录甲')

  // reference 选择器（单选）：点开 → 键入父名 → 点选（单选选中后下拉自动收起）
  const refItem = dialog.locator('.el-form-item', { hasText: '父记录' })
  await refItem.locator('.el-select__wrapper').click()
  await refItem.locator('input.el-select__input').fill(parentName)
  const opt = page.locator('.el-select-dropdown:visible .el-select-dropdown__item',
    { hasText: parentName })
  await expect(opt, '远程搜索应命中父记录').toBeVisible({ timeout: 10_000 })
  await opt.click()
  await dialog.getByRole('button', { name: '确定' }).click()

  const row = page.locator('.table-card .el-table__body tr', { hasText: '子记录甲' })
  await expect(row).toBeVisible()

  // 落库断言：reference 字段存父记录 id
  const listed = await listRecords(request, hChild.collection)
  const rec = listed.json.data.find((r: any) => r.name === '子记录甲')
  expect(rec?.pref, 'reference 字段应落库父记录 id').toBe(parentId)

  // 详情对话框：父名以 _ref_ 解析显示而非裸 id
  const viewer = await openViewer(page, '子记录甲')
  // reference 行：span.reference-link = _ref_pref_display = 父记录[displayField=name]
  await expect(viewer.locator('.reference-link')).toHaveText(parentName, { timeout: 10_000 })
  // 继承虚拟列 _ref_pref_name（allExpandedFields 展开 + 无列视图全字段进查看弹窗）：
  // 父名出现两次 —— reference 行 + 继承行
  await expect(viewer.locator('tr', { hasText: parentName })).toHaveCount(2)
  await screenshot(page, 'ref-inherit-detail')
  await viewer.getByRole('button', { name: '关闭', exact: true }).click()
})

// ---------------------------------------------------------------------------
// TD-C13 quoteSelect 多选：下拉点选两条 Q → quote 数组落库 → 编辑回显两项
// ---------------------------------------------------------------------------
test('TD-C13 quoteSelect 多选 UI 落库并回显', async ({ page, request }) => {
  const hQ = pages[4] // quoteQ
  const hA = pages[5] // quoteA
  const ts = Date.now()
  const q1Name = `引用源甲-${ts}`
  const q2Name = `引用源乙-${ts}`
  const q1 = await createRecord(request, hQ.collection, { name: q1Name })
  const q2 = await createRecord(request, hQ.collection, { name: q2Name })
  const q1Id = q1.json.id as string
  const q2Id = q2.json.id as string
  expect(q1.status, '引用源应创建成功').toBe(201)
  expect(q2.status, '引用源应创建成功').toBe(201)

  await gotoWithAuth(page, hA.path)
  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('请输入名称').fill('引用聚合甲')

  // quoteSelect 选择器（multiple）：点开 → 依次点选两条 Q
  const quoteItem = dialog.locator('.el-form-item', { hasText: '引用' })
  await quoteItem.locator('.el-select__wrapper').click()
  const dropdown = page.locator('.el-select-dropdown:visible')
  await dropdown.locator('.el-select-dropdown__item', { hasText: q1Name }).click()
  await dropdown.locator('.el-select-dropdown__item', { hasText: q2Name }).click()
  await screenshot(page, 'quote-multi-selected')
  await page.keyboard.press('Escape')
  await dialog.getByRole('button', { name: '确定' }).click()

  const row = page.locator('.table-card .el-table__body tr', { hasText: '引用聚合甲' })
  await expect(row).toBeVisible()

  // 落库断言：quoteSelect 值为记录 JSON 内的 id 数组
  const listed = await listRecords(request, hA.collection)
  const rec = listed.json.data.find((r: any) => r.name === '引用聚合甲')
  expect(rec?.quote, 'quote 应为数组且含两条 id').toEqual([q1Id, q2Id])

  // 重开编辑对话框：两个标签回显
  await row.getByRole('button', { name: '编辑' }).click()
  const editDialog = page.locator('.el-dialog:visible')
  const editQuoteItem = editDialog.locator('.el-form-item', { hasText: '引用' })
  await expect(editQuoteItem
    .locator('.el-select__selected-item .el-select__tags-text', { hasText: q1Name }))
    .toBeVisible({ timeout: 10_000 })
  await expect(editQuoteItem
    .locator('.el-select__selected-item .el-select__tags-text', { hasText: q2Name }))
    .toBeVisible()
  await editDialog.getByRole('button', { name: '取消' }).click()
})

// ---------------------------------------------------------------------------
// TD-C14 关系图谱对话框：API 造 relation → 查看弹窗「关系图谱」→ 图谱渲染 → 关闭
// ---------------------------------------------------------------------------
test('TD-C14 关系图谱对话框渲染与关闭', async ({ page, request }) => {
  const hA = pages[1] // relA
  const hB = pages[0] // relB
  // API 造数：A 记录 + relation → B（PUT /relations 正反向同步，relations.py:70-146）
  const bName = `图谱邻居-${Date.now()}`
  const b = await createRecord(request, hB.collection, { name: bName })
  const a = await createRecord(request, hA.collection, { name: '图谱中心甲' })
  expect(b.status, 'B 记录应创建成功').toBe(201)
  expect(a.status, 'A 记录应创建成功').toBe(201)
  const put = await api(request, 'PUT',
    `/relations/${hA.collection}/${a.json.id}/rel`,
    { targetCollection: hB.collection, targetField: 'rev', ids: [b.json.id] })
  expect(put.status, 'PUT /relations 应成功').toBe(200)

  await gotoWithAuth(page, hA.path)
  const viewer = await openViewer(page, '图谱中心甲')
  // 详情 relation 行应有邻居标签（_rel_rel_labels 解析）
  await expect(viewer.locator('.relation-tag-link', { hasText: bName }))
    .toBeVisible({ timeout: 10_000 })

  // footer「关系图谱」按钮（DynamicPage.vue:607-609）→ 图谱对话框
  await viewer.getByRole('button', { name: '关系图谱' }).click()
  // 以 .el-dialog__title 区分图谱弹窗与查看弹窗 footer 同名按钮
  const graphDialog = page.locator('.el-dialog:visible', {
    has: page.locator('.el-dialog__title', { hasText: '关系图谱' }),
  })
  await expect(graphDialog).toBeVisible({ timeout: 10_000 })
  // force-graph 在 .graph-container 注入 canvas（RelationGraphDialog.vue:16,525+）
  await expect(graphDialog.locator('.graph-container canvas'))
    .toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(2_000) // 力导布局稳定
  await screenshot(page, 'rel-graph-dialog')
  await graphDialog.getByRole('button', { name: '关闭', exact: true }).click()
  await expect(graphDialog).not.toBeVisible()
})
