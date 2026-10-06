/**
 * 族F/G/H L3 自动化 UI 旅程（TD-F16/G08/H06，共 3 例）。
 *
 * 选择器锚点全部按组件实读落定（2026-10-07 实读，file:line 见 task-6-report）：
 * - DataTable.vue:130-147   行「⋯」溢出下拉：触发按钮 .row-actions-trigger，
 *   菜单逐行挂 body（每行一份），必须 .el-dropdown-menu__item:visible 限定当前展开项。
 * - DynamicPage.vue:270-287 extra-actions 插槽为 visibleRowActionsFor(row) 的每个
 *   行操作渲染 el-dropdown-item（文本 = action.label，点击调 rowActionRunnerRef.run）。
 * - DynamicPage.vue:291-296 RowActionRunner 挂载；RowActionRunner.vue:105-126
 *   run()：有 confirmText 先弹 ElMessageBox.confirm（确定/取消），无 paramFields
 *   直接 submit；:149 submit 成功 ElMessage.success('已提交')，:151-156 响应
 *   status==='running' 时启动 5s 行状态轮询。
 * - 终态回写：server/routes/dynamic.py:1463 POST /<collection>/<rid>/row-actions/
 *   <aid>/run → row_action_engine.run_action 先原子把状态字段写 runningValue，
 *   再后台线程 _run_webhook（row_action_engine.py:263-299）——url 指向死端口时
 *   fire_webhook_rule（webhook_engine.py:169-172/311-390）必然失败，finally 把
 *   failedValue 回写该行 → 'failed' 是确定性终态（比 done 更可断言）。
 * - 行操作拓扑约束：server/utils/row_action_validate.py:63-76 绑定规则必须
 *   triggerEvent='manual'；:93-104 配了 statusField 必须同时给 runningValue/
 *   doneValue/failedValue；状态字段须是本页面标量字段（ra_status，select 四值）。
 *   rowActions 经 PUT /pageConfigs/<id> 落库（server/routes/page_configs.py:178-227）。
 * - 列视图：ViewSelector.vue:2-31 工具栏 .view-selector el-select（公共视图分组），
 *   DynamicPage.vue:156-159 @select → handleViewSelect → columnViewStore.selectView
 *   （columnView.ts:111）；columnView.ts:126-146 getTableColumns 按
 *   currentView.columns[{fieldId,visible,...}] 过滤字段 → DataTable 可见列变化。
 *   视图经 POST /column-views/<pageId>/views 创建（server/routes/column_views.py:67-127），
 *   columns 项形态 {fieldId: 字段id, visible, order, width}（src/types/columnView.ts:20-25）。
 * - 评论/时间线：DynamicPage.vue:416-440 查看记录弹窗，:585-591 「评论 / 变更历史」
 *   divider + RecordTimeline（destroy-on-close 每次重挂、onMounted 拉
 *   GET /timeline/<collection>/<rid>，timeline.py:18-76 评论+操作日志合并）。
 *   RecordTimeline.vue:49-53 添加评论 textarea placeholder「添加评论...」、:63 发送、
 *   :15-16 每条评论 编辑/删除（admin 可编辑任意评论）、:19-25 行内编辑区 保存。
 *   API 评论经 POST /comments/<collection>/<rid>（server/routes/comments.py:32-66）。
 */
import { test, expect } from '@playwright/test'
import {
  api, createDataPage, createRecord, deleteDataPage, gotoWithAuth, listRecords,
  screenshot, type DataPageHandle, type FieldLite,
} from './helpers'

test.setTimeout(120_000)

const pages: DataPageHandle[] = []
/** TD-F16 拓扑：manual webhook 规则（死端口），afterAll 回收 */
let ruleId: string | null = null

/** TD-F16 页字段：名称 + 审批状态（ra_status，select 四值 = 行操作状态字段的
 *  pending/running/done/failed 全集，UI 按 options 映射显示中文标签）。 */
const F_FIELDS: FieldLite[] = [
  { id: 'f1', label: '名称', fieldName: 'name', controlType: 'text',
    required: true, order: 1, placeholder: '请输入名称' },
  { id: 'f2', label: '审批状态', fieldName: 'ra_status', controlType: 'select',
    required: false, order: 2,
    options: [
      { label: '待提交', value: 'pending' },
      { label: '执行中', value: 'running' },
      { label: '已通过', value: 'done' },
      { label: '失败', value: 'failed' },
    ] },
]

/** 列视图 columns 项（src/types/columnView.ts:20-25 ColumnConfigItem） */
const col = (fieldId: string, order: number) => ({ fieldId, visible: true, order, width: 'auto' })

test.beforeAll(async ({ request }) => {
  // TD-F16 页（行动作拓扑的 rowActions 在用例内建：需先有 webhook 规则）
  pages.push(await createDataPage(request, 'F', 'auto', F_FIELDS))

  // TD-G08 页 + 两个公开列视图（全列 / 仅名称）
  const g = await createDataPage(request, 'G', 'view')
  pages.push(g)
  const vAll = await api(request, 'POST', `/column-views/${g.pageId}/views`, {
    name: '全部字段', isPublic: true,
    columns: [col('f1', 1), col('f2', 2), col('f3', 3)],
  })
  expect(vAll.status, JSON.stringify(vAll.json)).toBe(201)
  const vName = await api(request, 'POST', `/column-views/${g.pageId}/views`, {
    name: '仅名称', isPublic: true,
    columns: [col('f1', 1)],
  })
  expect(vName.status, JSON.stringify(vName.json)).toBe(201)

  // TD-H06 页（记录与 API 评论在用例内建）
  pages.push(await createDataPage(request, 'H', 'timeline'))
})

test.afterAll(async ({ request }) => {
  if (ruleId) await api(request, 'DELETE', `/webhook/rules/${ruleId}`)
  for (const h of [...pages].reverse()) await deleteDataPage(request, h)
})

// ---------------------------------------------------------------------------
// TD-F16 行动作 UI 触发：行「⋯」→ 审批 → 确认 → 已提交 → 失败回写（死端口）
// ---------------------------------------------------------------------------
test('TD-F16 行动作 UI 触发：审批按钮 → 异步执行 → 失败回写状态字段', async ({ page, request }) => {
  const h = pages[0]
  const recName = `F16记录-${Date.now()}`

  // 拓扑：manual webhook 规则指向死端口（127.0.0.1:1 必然连接拒绝，timeout 2
  // retries 0 → 毫秒级失败），页 rowActions 绑定 approve（校验规则见文件头注释）
  ruleId = `whrule-F16-${Date.now()}`
  const rule = await api(request, 'POST', '/webhook/rules', {
    id: ruleId, name: `F16死端口规则-${Date.now()}`, description: 'data-full e2e 行动作规则',
    enabled: true, sourceCollections: [h.collection], triggerEvent: 'manual',
    webhookUrl: 'http://127.0.0.1:1/x', timeout: 2, retries: 0, executionOrder: 9999,
  })
  expect(rule.status, JSON.stringify(rule.json)).toBe(201)
  const ra = await api(request, 'PUT', `/pageConfigs/${h.pageId}`, {
    rowActions: [{
      id: 'approve', label: '审批', actionType: 'webhook', webhookRuleId: ruleId,
      confirmText: '确认执行「审批」？执行器将异步处理，并把结果回写到审批状态字段。',
      statusField: 'ra_status', runningValue: 'running',
      doneValue: 'done', failedValue: 'failed', enabled: true,
    }],
  })
  expect(ra.status, JSON.stringify(ra.json)).toBeLessThan(300)

  // UI 建一条记录
  await gotoWithAuth(page, h.path)
  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('请输入名称').fill(recName)
  await dialog.getByRole('button', { name: '确定' }).click()
  // .first()：UI 新增后偶发同记录瞬时渲染两行（前端 tableData/cache 并发刷新
  // 竞态，后复检 API 始终单条——产品瑕疵记录于 task-6-report，不属于本例断言面）
  const row = page.locator('.table-card .el-table__body tr', { hasText: recName }).first()
  await expect(row).toBeVisible()
  if (await page.locator('.table-card .el-table__body tr', { hasText: recName }).count() > 1) {
    const dbg = await listRecords(request, h.collection)
    console.log('DUP_ROWS_API_RECORDS', JSON.stringify(
      (dbg.json.data || []).map((r: { id: string; name: string }) => `${r.id}|${r.name}`)))
  }

  // 行「⋯」下拉出现「审批」项 → 点击 → RowActionRunner 二次确认框
  await row.locator('.row-actions-trigger').click()
  const approveItem = page.locator('.el-dropdown-menu__item:visible', { hasText: '审批' })
  await expect(approveItem).toBeVisible()
  await approveItem.click()
  const confirmBox = page.locator('.el-message-box:visible', { hasText: '审批' })
  await expect(confirmBox).toBeVisible()
  await screenshot(page, 'automation-row-action')
  await confirmBox.getByRole('button', { name: '确定' }).click()

  // 提交反馈（RowActionRunner.vue:149 ElMessage '已提交'），行状态字段出现
  // running/终态反馈（死端口失败在毫秒级，running 窗口极短，二者皆算 UI 反馈）
  await expect(page.locator('.el-message', { hasText: '已提交' }))
    .toBeVisible({ timeout: 10_000 })
  await expect(row).toContainText(/执行中|失败/, { timeout: 15_000 })

  // API 轮询确定性终态：失败回写 failed（row_action_engine._run_webhook finally）
  await expect.poll(async () => {
    const listed = await listRecords(request, h.collection)
    const rec = (listed.json.data || []).find((r: { name: string }) => r.name === recName)
    return rec ? String(rec.ra_status ?? '') : ''
  }, { message: 'ra_status 应回写 failed（死端口 webhook 终态）', timeout: 30_000 })
    .toBe('failed')

  // UI 复核终态显示（select 值 → label 映射：DataTable.vue:527-533）
  await page.reload()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: recName }).first())
    .toContainText('失败', { timeout: 15_000 })
})

// ---------------------------------------------------------------------------
// TD-G08 列视图切换 UI：公共视图切换改变表格可见列
// ---------------------------------------------------------------------------
test('TD-G08 列视图切换 UI：选「仅名称」数量列消失，切回恢复', async ({ page, request }) => {
  const h = pages[1]
  await createRecord(request, h.collection, { name: `G08记录-${Date.now()}`, qty: 3 })
  await gotoWithAuth(page, h.path)

  // 初始无视图：全字段可见（columnView.ts:127-129 currentView 为空 → 全字段）
  const header = page.locator('.table-card .el-table__header')
  await expect(header).toContainText('名称')
  await expect(header).toContainText('数量')

  // 工具栏视图切换器（ViewSelector el-select）→ 公共视图「仅名称」
  await page.locator('.view-selector .el-select__wrapper').click()
  await page.locator('.el-select-dropdown:visible .el-select-dropdown__item',
                     { hasText: '仅名称' }).click()
  // 视图列配置生效：数量列表头消失，名称列保留
  await expect(header).not.toContainText('数量')
  await expect(header).toContainText('名称')
  await screenshot(page, 'view-switched')

  // 切回「全部字段」→ 数量列恢复
  await page.locator('.view-selector .el-select__wrapper').click()
  await page.locator('.el-select-dropdown:visible .el-select-dropdown__item',
                     { hasText: '全部字段' }).click()
  await expect(header).toContainText('数量')
  await screenshot(page, 'view-restored')
})

// ---------------------------------------------------------------------------
// TD-H06 评论/时间线 UI：查看弹窗时间线面板 发/编/删评论
// ---------------------------------------------------------------------------
test('TD-H06 评论/时间线 UI：API 评论可见，UI 发/编/删自己的评论', async ({ page, request }) => {
  const h = pages[2]
  const recName = `H06记录-${Date.now()}`
  const rec = await createRecord(request, h.collection, { name: recName })
  const recordId = rec.json.id as string
  expect(rec.status).toBe(201)

  // API 先发一条评论（timeline 合并显示）
  const apiComment = `DTEST-H-API评论-${Date.now()}`
  const c = await api(request, 'POST', `/comments/${h.collection}/${recordId}`,
                      { content: apiComment })
  expect(c.status, JSON.stringify(c.json)).toBe(201)

  // 打开查看弹窗 → 「评论 / 变更历史」区 → API 评论可见
  await gotoWithAuth(page, h.path)
  const row = page.locator('.table-card .el-table__body tr', { hasText: recName }).first()
  await expect(row).toBeVisible()
  await row.locator('.row-actions-trigger').click()
  await page.locator('.el-dropdown-menu__item:visible', { hasText: '查看' }).click()
  const viewer = page.locator('.el-dialog:visible', { hasText: '查看记录' })
  await expect(viewer).toBeVisible()
  await expect(viewer.locator('.el-divider', { hasText: '评论 / 变更历史' })).toBeVisible()
  await expect(viewer.locator('.timeline-comment .comment-content', { hasText: apiComment }))
    .toBeVisible({ timeout: 10_000 })

  // UI 发一条新评论
  const uiComment = 'DTEST-H-UI评论'
  await viewer.getByPlaceholder('添加评论...').fill(uiComment)
  await viewer.getByRole('button', { name: '发送' }).click()
  const uiBlock = viewer.locator('.timeline-comment', { hasText: uiComment })
  await expect(uiBlock).toBeVisible({ timeout: 10_000 })
  await screenshot(page, 'timeline-panel-posted')

  // UI 编辑自己刚发的评论
  const edited = `${uiComment}-已编辑`
  await uiBlock.getByRole('button', { name: '编辑' }).click()
  await viewer.locator('.comment-edit textarea').fill(edited)
  await viewer.locator('.comment-edit').getByRole('button', { name: '保存' }).click()
  await expect(viewer.locator('.timeline-comment .comment-content', { hasText: edited }))
    .toBeVisible({ timeout: 10_000 })

  // UI 删除该评论（RecordTimeline 删除无确认框，直接删 + 重拉时间线）
  await uiBlock.getByRole('button', { name: '删除' }).click()
  await expect(viewer.locator('.timeline-comment', { hasText: uiComment })).toHaveCount(0, { timeout: 10_000 })
  // API 评论不受影响
  await expect(viewer.locator('.timeline-comment .comment-content', { hasText: apiComment }))
    .toBeVisible()
  await screenshot(page, 'timeline-panel')
})
