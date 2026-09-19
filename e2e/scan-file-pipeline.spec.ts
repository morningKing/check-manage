/**
 * AI 定时任务 E2E：读记录附件处理 + 产出文件回填数据行。
 * 验证扫描流水线：页面配置声明 file 字段 → 记录附件拷入子会话 →
 * AI 读文件提取关键字 → jsonb_set 回写 → extracted_keyword 可查。
 */
import fs from 'node:fs'
import { test, expect } from '@playwright/test'

const AUTH_FILE = 'e2e/.auth/admin.json'

test.beforeEach(async ({ context }) => {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))
  await context.addInitScript((entries: Record<string, string>) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, auth)
})

async function api(request: import('@playwright/test').APIRequestContext,
                   method: string, path: string, data?: any): Promise<{ status: number; json: any }> {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))
  const token = JSON.parse(auth['check-manage:token'])
  const r = await request.fetch(`/api${path}`, {
    method, data: data === undefined ? undefined : JSON.stringify(data),
    headers: { Authorization: `Bearer ${token}` },
  })
  let json: any = null
  try { json = await r.json() } catch { /* 204 */ }
  if (r.status() >= 400) console.log(`[API ${r.status()} ${method} ${path}]`, JSON.stringify(json).slice(0, 150))
  return { status: r.status(), json }
}

test('AI 定时任务：读附件提取关键字 + 回写数据行', async ({ page, request }) => {
  test.setTimeout(420_000)
  const tag = `e2escan-${Date.now()}`

  // ① 页面配置（声明 file 字段供扫描引擎拷贝附件到子会话）
  const coll = `scan_${Date.now()}`
  const pc = await api(request, 'POST', '/pageConfigs', {
    id: `page-${coll}`, name: tag, collection: coll,
    fields: [
      { fieldName: 'title', label: '标题', controlType: 'input' },
      { fieldName: 'input_doc', label: '输入附件', controlType: 'file' },
    ],
  })
  expect(pc.status, `建页面配置 ${pc.status}`).toBeLessThan(300)

  // ② 上传附件 → 建记录并挂附件
  const token = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))['check-manage:token']
  const tokenStr = typeof token === 'string' ? token.replace(/^"|"$/g, '') : token
  const up = await request.fetch('/api/data-files/upload', {
    method: 'POST',
    headers: { Authorization: `Bearer ${tokenStr}` },
    multipart: {
      file: { name: 'scan-input.txt', mimeType: 'text/plain',
        buffer: Buffer.from('关键字 BAIZE-E2E-OK') },
    },
  })
  expect(up.status(), '上传输入附件').toBeLessThan(400)
  const fileUid = ((await up.json()) as any).id as string

  const rec = { id: `rec_${Date.now()}` }
  const cr = await api(request, 'POST', `/${coll}`, {
    id: rec.id,
    title: `${tag} 记录`,
    scan_status: '待处理',
    input_doc: [{ uid: fileUid, name: 'scan-input.txt' }],
  })
  expect(cr.status, `建记录 ${cr.status}`).toBeLessThan(300)

  // ③ 创建扫描任务
  const task = await api(request, 'POST', '/ai-scan-tasks', {
    name: tag,
    collection: coll,
    branchId: 'main',
    statusField: 'scan_status',
    pendingValue: '待处理',
    runningValue: '处理中',
    doneValue: '已处理',
    failedValue: '处理失败',
    promptTemplate: 'uploads/record.md 与 uploads/attachments/ 里有记录数据。'
      + '请读取附件文件的内容，把文件中出现的「关键字」的值提取出来。'
      + '最后按输出契约输出 JSON。',
    fieldMapping: [{ jsonKey: 'keyword', column: 'extracted_keyword', required: true }],
    maxRecordsPerScan: 5,
  })
  expect(task.status, '创建扫描任务').toBeLessThan(400)
  const taskId = task.json.id

  // ④ 立即触发
  const run = await api(request, 'POST', `/ai-scan-tasks/${taskId}/run-now`)
  expect(run.status, '触发扫描').toBeLessThan(400)

  // ⑤ 轮询记录状态（最长 6 分钟）
  let finalStatus = ''
  let extracted = ''
  for (let i = 0; i < 60; i++) {
    await page.waitForTimeout(6000)
    const rr = await api(request, 'GET', `/${coll}`)
    const cur = ((rr.json.data || []) as any[]).find(x => x.id === rec.id)
    if (!cur) continue
    finalStatus = cur.scan_status ?? ''
    extracted = cur.extracted_keyword ?? ''
    if (['已处理', '处理失败'].includes(finalStatus)) break
  }
  console.log(`[scan] status=${finalStatus} extracted=${extracted}`)
  expect(finalStatus, '扫描收敛').toBe('已处理')
  expect(extracted, '提取关键字').toContain('BAIZE-E2E-OK')
})
