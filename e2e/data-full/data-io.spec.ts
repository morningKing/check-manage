/**
 * 族E 导入 / 导出 UI 旅程 —— L3 真实链路（TD-E17–E18，共 2 例）。
 *
 * 组件实读锚点（src/views/dynamic/DynamicPage.vue）：
 * - 操作菜单（el-dropdown trigger="click"，handleMoreCommand）：
 *   command="export"（导出 Excel，:180）→ handleExport → exportToExcel；
 *   command="import"（导入数据，:189，v-if=canCreate，admin 满足）→
 *   handleImportCommand → fileInputRef.click()（隐藏 input[type=file]，~:854，
 *   accept=.xlsx/.xls/.json）→ handleFileSelected → multiImport.setFiles →
 *   「待导入文件」暂存弹窗 → 「开始导入（N 个文件）」→ 进度/结果弹窗
 *   （entry.status='success' → el-tag 成功，明细「成功 N 条」）→
 *   有写入则 loadPageData 刷新表格。
 * - 列名约定（src/utils/excelParseCore.ts buildHeaderToField）：表头 = 字段
 *   label（「名称」「数量」），同时兼容 fieldName。
 * - 导入落库（src/utils/importPageRecords.ts）：id 由前端逐行生成
 *   （makeImportRowId 保序行 id），分批 POST /<collection>/batch-create，完成后
 *   POST /importRuns 登记（fileName、counts；failedCount=0 → status='success'，
 *   server/routes/import_runs.py:52）。
 * - 导出（src/utils/excel.ts exportToExcel）：XLSX.writeFile(wb, `${name}.xlsx`)，
 *   name = pageConfig.name，表头 = 字段 label，sheet 名「数据」。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test, expect } from '@playwright/test'
import * as XLSX from 'xlsx'
import {
  api, createDataPage, createRecord, deleteDataPage, gotoWithAuth,
  screenshot, type FieldLite,
} from './helpers'

test.setTimeout(120_000)

// 名称/数量两字段（导入表头即 label「名称」「数量」，匹配走 buildHeaderToField）
const IO_FIELDS: FieldLite[] = [
  { id: 'f1', label: '名称', fieldName: 'name', controlType: 'text',
    required: true, order: 1, placeholder: '请输入名称' },
  { id: 'f2', label: '数量', fieldName: 'qty', controlType: 'number',
    required: false, order: 2, placeholder: '请输入数量' },
]

let h: Awaited<ReturnType<typeof createDataPage>>

test.beforeAll(async ({ request }) => {
  h = await createDataPage(request, 'E', 'io', IO_FIELDS)
})

test.afterAll(async ({ request }) => {
  if (h) await deleteDataPage(request, h) // 导入的行随配置回收链一并删除
})

/** 内存生成真实 xlsx（与被测前端同源的 SheetJS：根 node_modules xlsx@0.18.5） */
function makeImportFile(fileName: string, rows: [string, number][]): {
  name: string; mimeType: string; buffer: Buffer
} {
  const ws = XLSX.utils.aoa_to_sheet([['名称', '数量'], ...rows])
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '导入数据')
  const buffer = XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' }) as Buffer
  return {
    name: fileName,
    mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    buffer,
  }
}

test('TD-E17 导入 UI 全链路：xlsx → 暂存确认 → 落库 → 导入历史', async ({ page, request }) => {
  const ts = Date.now()
  const rows: [string, number][] = [
    [`导入甲-${ts}`, 11], [`导入乙-${ts}`, 22], [`导入丙-${ts}`, 33],
  ]
  const fileName = `导入冒烟-${ts}.xlsx`

  await gotoWithAuth(page, h.path)
  await expect(page.getByRole('button', { name: '新增' })).toBeVisible()

  // 操作菜单「导入数据」→ handleImportCommand → 隐藏 input[type=file].click()。
  // 以 filechooser 事件承接该次 click 并对同一 input 本体 setFiles（等价
  // setInputFiles，且天然规避 display:none 输入的可见性问题）。
  await page.getByRole('button', { name: '操作' }).click()
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.locator('.el-dropdown-menu__item', { hasText: '导入数据' }).click(),
  ])
  await chooser.setFiles(makeImportFile(fileName, rows))

  // 暂存弹窗「待导入文件」→ 确认开始导入
  const stageDialog = page.locator('.el-dialog:visible', { hasText: '待导入文件' })
  await expect(stageDialog).toBeVisible()
  await expect(stageDialog.locator('.import-stage-name', { hasText: fileName }))
    .toBeVisible()
  await stageDialog.getByRole('button', { name: /开始导入/ }).click()

  // 结果反馈：结果行 el-tag「成功」+ 明细「成功 3 条」（createImportRun 已在
  // uploadImportedRecords 内完成，此刻历史已落库）
  const resultDialog = page.locator('.el-dialog:visible', { hasText: '导入数据' })
  const resultRow = resultDialog.locator('.import-file-result-row', { hasText: fileName })
  await expect(resultRow.locator('.el-tag', { hasText: '成功' }))
    .toBeVisible({ timeout: 30_000 })
  await expect(resultRow).toContainText('成功 3 条')
  await resultDialog.getByRole('button', { name: '确定' }).click()

  // 表格刷新渲染导入行（loadPageData）
  for (const [name] of rows) {
    await expect(page.locator('.table-card .el-table__body tr', { hasText: name }))
      .toBeVisible({ timeout: 15_000 })
  }
  await screenshot(page, 'io-imported-table')

  // API 断言 3 行落库：id 由前端逐行生成（非空），名称/数量与 sheet 一致。
  // 行 id 带 6 位随机后缀（makeImportRowId），重复导入会追加新行——按本次
  // 时间戳后缀过滤后应恰好 3 条，既精确又对失败重跑友好。
  const listed = await listRecordsAll(request, h.collection)
  const mine = listed.filter((r) => String(r.name).endsWith(`-${ts}`))
  expect(mine.length, '本次导入应恰好落库 3 行').toBe(3)
  for (const [name, qty] of rows) {
    const rec = mine.find((r) => r.name === name)
    expect(rec, `${name} 应落库`).toBeTruthy()
    expect(String(rec.qty), `${name} 数量`).toBe(String(qty))
    expect(rec.id, '行 id 应由前端生成').toBeTruthy()
  }

  // 导入历史：GET /importRuns 断言新增一条 success 记录（fileName 匹配）
  const runs = await api(request, 'GET',
    `/importRuns?pageId=${encodeURIComponent(h.pageId)}&collection=${encodeURIComponent(h.collection)}`)
  expect(runs.status).toBe(200)
  const run = (runs.json?.runs || []).find((r: any) => r.fileName === fileName)
  expect(run, `importRuns 应有 fileName=${fileName} 的记录`).toBeTruthy()
  expect(run.status, '全量导入成功 → status=success').toBe('success')
  expect(run.successCount).toBe(3)
  expect(run.createdCount).toBe(3)
})

// TD-E18 复用 TD-E17 落库的行（同文件声明序执行，单 worker），另补 1 行 API
// 播种保证 ≥1 行 —— 即使前序用例失败，导出也不因「暂无数据可导出」空转。
test('TD-E18 导出 Excel 下载', async ({ page, request }) => {
  const marker = `导出针-${Date.now()}`
  await createRecord(request, h.collection, { name: marker, qty: 1 })
  await gotoWithAuth(page, h.path)
  await expect(page.getByRole('button', { name: '新增' })).toBeVisible()

  // 操作菜单「导出 Excel」→ handleExport → exportToExcel（XLSX.writeFile 触发下载）
  await page.getByRole('button', { name: '操作' }).click()
  const downloadPromise = page.waitForEvent('download')
  await page.locator('.el-dropdown-menu__item', { hasText: '导出 Excel' }).click()
  const download = await downloadPromise

  // suggestedFilename：pageConfig.name + '.xlsx'（exportToExcel 的 `${name}.xlsx`）
  expect(download.suggestedFilename()).toBe(`${h.name}.xlsx`)

  // 落盘到系统临时目录核对大小（避免在仓库里累积二进制产物；截图另有 png 证据）
  const target = path.join(os.tmpdir(), `io-export-download-${marker}.xlsx`)
  await download.saveAs(target)
  const size = fs.statSync(target).size
  expect(size, '导出文件非空').toBeGreaterThan(0)
  await screenshot(page, 'io-export-download')

  // 内容校验：sheet「数据」，表头 = 字段 label，含播种标记行
  // （playwright 转译环境里 xlsx 无 fs 便捷入口 readFile，用核心 API read）
  const wb = XLSX.read(fs.readFileSync(target), { type: 'buffer' })
  const sheetName = wb.SheetNames[0]
  expect(sheetName).toBe('数据')
  const aoa = XLSX.utils.sheet_to_json<unknown[]>(wb.Sheets[sheetName], { header: 1 })
  const headers = (aoa[0] || []).map(String)
  expect(headers, '表头应为字段 label').toEqual(expect.arrayContaining(['名称', '数量']))
  const flat = aoa.map((r) => r.map(String).join('|')).join('\n')
  expect(flat, '导出内容应含播种记录').toContain(marker)
})

/** 全量拉取（绕过分页），返回 records 数组 */
async function listRecordsAll(request: any, collection: string): Promise<any[]> {
  const res = await api(request, 'GET', `/${collection}?all=true`)
  expect(res.status).toBe(200)
  return res.json?.data || []
}
