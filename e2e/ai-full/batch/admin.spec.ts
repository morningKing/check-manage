/**
 * AI 批任务管理域 spec（管理页闭环，全确定性：fail-fast 驱动 0 LLM）。
 *
 * 覆盖（API 面 4 例 + UI 面 1 例）：
 *  1. 跨用户列表筛选（keyword=批名 / status）与跨用户详情 + 子任务消息端点；
 *  2. admin reexecute（单子）与 retry-failed（全批）生效 + 终态软删子任务；
 *  3. tool-calls 与 attempt-timeline 端点（会话级路由）；
 *  4. AdminBatchFiles 文件面：uploads 分组可见 / download / 导入 data_files 幂等。
 *
 * 相对 brief 草稿的修正（已逐一对照服务端实现核实）：
 *  - 用例 1：keyword 筛选匹配的是批任务**名称**（b.name ILIKE，batch_repo.py:1322），
 *    草稿的 keyword=<bid> 永不命中——改为用唯一批名筛选；批任务改由 secondUser
 *    创建，断言 ownerUsername 标注为该用户，跨用户可见性才是真证明（admin 建批
 *    自己看自己不算跨用户）。管理面出参是白名单 camelCase：列表 {items:[{batchId,
 *    ownerUsername,...}], total}（ai_batch_admin.py:38-67）、详情 sessions[].sessionId
 *    （:57-67），断言直落真实键名。
 *  - 用例 2：软删后子任务是**从详情消失**而非带 deleted_at——owner 详情
 *    （batch_repo.py:379）与 admin 详情（:1341）的 SELECT 都过滤
 *    deleted_at IS NULL，且 admin _session_out 白名单（ai_batch_admin.py:57）根本
 *    不含该列；顺带断言计数按未删除子任务重算（单子批 total 归 0）。
 *    reexecute 重排瞬态（pending/running）只做 8s 尽力观察不判失败：worker 认领
 *    可在毫秒级抢先、fail-fast 再败（同 retry-reexecute.spec 用例 3 的容忍注记），
 *    权威断言是收敛终态 + retried=1。
 *  - 用例 3：attempt-timeline 是**会话级**路由 /ai/chat/admin/batches/sessions/
 *    <sid>/attempt-timeline（ai_batch_admin.py:305-307），草稿带 <bid> 前缀的
 *    路径 404。fail-fast 在 create_attempt（batch_engine.py:1444）之前 return
 *    （:1433），天然无 attempt 行——dbSeed 种一条使读断言有内容（清理随
 *    ai_chat_sessions FK ON DELETE CASCADE 级联）；tool-calls 对无
 *    opencode_session_id 的子任务返回 {calls: []}（:270-305）。
 *  - 用例 4：文件清单形状 {files:[{name,path,dir,size}], truncated}
 *    （workspace_outputs.py:20-40，uploads 项 path='uploads/in-0.txt'）；
 *    二次导入必为 existing——已入库记录命中即返 existing
 *    （session_file_import.py:57）；下载体断言写回上传原文。
 *  - UI 例选择器全部对照 src/views/admin/AiBatchAdmin.vue 实文案：列表行「详情」
 *    link button（:51）、抽屉内重试按钮「重试全部失败（N）」（:73）、子任务行操作
 *    查看对话/产出文件/工具调用/重跑/删除（:88-94）、成功 toast「已重置 N 个失败
 *    的子任务」（:300）与「已软删除(原状态:…)」（:318）、删除确认
 *    ElMessageBox「确定删除?」（:313-315）。路由 /admin/ai-execution?tab=batches
 *    由 SettingsTabShell normalize(route.query.tab) 落到「批量执行」tab
 *    （settingsCatalog.ts:87-89）。
 *
 * 断言直连后端 3002（理由见 batch-helpers.ts 头注释）。
 */
import { test, expect } from '@playwright/test'
import {
  API, authHeaders, cleanupBatch, getDetail, waitBatchTerminal, waitFor,
} from './batch-helpers'
import { adminTokenCached, dbSeed, failFastBatch, secondUser, tag } from './toolbox'
import { gotoWithAuth, screenshot } from '../helpers'

// 批任务收敛轮询（fail-fast 秒级）+ UI 全程，60s 默认不够
test.setTimeout(240_000)

test.describe('admin API 面', () => {
  test('跨用户列表筛选（status/keyword）与详情+子任务消息', async () => {
    const admin = await adminTokenCached()
    const userB = await secondUser('admfil')
    const name = tag('admfil')
    const bid = await failFastBatch(userB.token, { files: 1, name })
    try {
      await waitFor(async () => (await getDetail(userB.token, bid)).batch.status === 'failed',
        90_000, 'userB 批 fail-fast failed')

      // keyword（批名 ILIKE）跨用户命中，且归属标注为创建者 userB
      const hit = await (await fetch(
        `${API}/ai/chat/admin/batches?keyword=${encodeURIComponent(name)}`,
        { headers: authHeaders(admin) })).json()
      expect(Array.isArray(hit.items)).toBe(true)
      const mine = hit.items.find((i: any) => i.batchId === bid)
      expect(mine, `keyword 筛选未命中 ${name}`).toBeTruthy()
      expect(mine.ownerUsername).toBe(userB.username)

      // status 组合筛选命中
      const byStatus = await (await fetch(
        `${API}/ai/chat/admin/batches?status=failed&keyword=${encodeURIComponent(name)}`,
        { headers: authHeaders(admin) })).json()
      expect((byStatus.items ?? []).some((i: any) => i.batchId === bid)).toBe(true)
      // 反例：status=completed 不应带出该 failed 批
      const byWrong = await (await fetch(
        `${API}/ai/chat/admin/batches?status=completed&keyword=${encodeURIComponent(name)}`,
        { headers: authHeaders(admin) })).json()
      expect((byWrong.items ?? []).some((i: any) => i.batchId === bid)).toBe(false)

      // 管理员跨用户详情：白名单出参 batchId/sessions[].sessionId/name
      const dres = await fetch(`${API}/ai/chat/admin/batches/${bid}`, { headers: authHeaders(admin) })
      expect(dres.status).toBeLessThan(300)
      const detail = await dres.json()
      expect(detail.batch?.batchId).toBe(bid)
      expect(detail.batch?.ownerUsername).toBe(userB.username)
      expect(detail.sessions?.length).toBe(1)
      expect(detail.sessions[0].sessionId).toBeTruthy()
      expect(detail.sessions[0].name).toBe('in-0.txt')

      // 子任务消息端点（跨用户只读；fail-fast 子任务消息数不定，只断言契约形状）
      const sid = detail.sessions[0].sessionId
      const mres = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/messages`,
        { headers: authHeaders(admin) })
      expect(mres.status).toBeLessThan(300)
      const msgs = await mres.json()
      expect(Array.isArray(msgs.messages)).toBe(true)
      expect(typeof msgs.total).toBe('number')
      expect(typeof msgs.truncated).toBe('boolean')
    } finally {
      await cleanupBatch(userB.token, bid)
      await userB.cleanup()
    }
  })

  test('admin retry-failed 与单子 reexecute 生效；终态软删子任务', async () => {
    const admin = await adminTokenCached()
    const bid = await failFastBatch(admin, { files: 1 })
    try {
      await waitFor(async () => (await getDetail(admin, bid)).batch.status === 'failed',
        90_000, '第一轮 failed')
      const sid = (await getDetail(admin, bid)).sessions[0].id

      // 单子重跑 → 200 {reexecuted: true}
      const rex = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/reexecute`,
        { method: 'POST', headers: authHeaders(admin) })
      expect(rex.status).toBeLessThan(300)
      expect((await rex.json()).reexecuted).toBe(true)
      // 重排瞬态尽力观察（worker 认领可毫秒级抢先再 fail-fast，抓不到不算失败）
      try {
        await waitFor(async () =>
          (await getDetail(admin, bid)).sessions[0].status !== 'failed' || null, 8_000, '瞬态', 300)
      } catch { /* 已抢先收敛 */ }
      // 第二轮 fail-fast 收敛
      await waitBatchTerminal(admin, bid, 120_000, 2000)

      // 全批重试失败 → retried 恰为 1（生效的权威证明）
      const retry = await fetch(`${API}/ai/chat/admin/batches/${bid}/retry-failed`,
        { method: 'POST', headers: authHeaders(admin) })
      expect(retry.status).toBeLessThan(300)
      expect((await retry.json()).retried).toBe(1)
      // 第三轮 fail-fast 收敛到终态（retry-failed 重排确实重新派发）
      await waitBatchTerminal(admin, bid, 120_000, 2000)

      // 终态软删 → {deleted: true, status: 原状态, deletedAt}
      const del = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}`,
        { method: 'DELETE', headers: authHeaders(admin) })
      expect(del.status).toBeLessThan(300)
      const delBody = await del.json()
      expect(delBody.deleted).toBe(true)
      expect(delBody.status).toBe('failed')
      // 软删后子任务从两侧详情消失（SELECT 过滤 deleted_at IS NULL，
      // batch_repo.py:379/1341），计数按未删除子任务重算 → 单子批 total 归 0
      const afterOwner = await getDetail(admin, bid)
      expect(afterOwner.sessions.filter((s: any) => s.id === sid).length).toBe(0)
      const afterAdmin = await (await fetch(`${API}/ai/chat/admin/batches/${bid}`,
        { headers: authHeaders(admin) })).json()
      expect(afterAdmin.sessions.filter((s: any) => s.sessionId === sid).length).toBe(0)
      expect(afterAdmin.batch.total).toBe(0)
    } finally { await cleanupBatch(admin, bid) }
  })

  test('tool-calls 与 attempt-timeline 端点', async () => {
    const admin = await adminTokenCached()
    const bid = await failFastBatch(admin, { files: 1 })
    try {
      await waitFor(async () => (await getDetail(admin, bid)).batch.status === 'failed',
        90_000, 'failed')
      const sid = (await getDetail(admin, bid)).sessions[0].id

      // tool-calls：fail-fast 早于 create_session（batch_engine.py:1433）→
      // 无 opencode_session_id → {calls: []}（ai_batch_admin.py:292-293）
      const tc = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/tool-calls`,
        { headers: authHeaders(admin) })
      expect(tc.status).toBeLessThan(300)
      expect(Array.isArray((await tc.json()).calls)).toBe(true)

      // attempt-timeline：会话级路由（不带 batch 前缀）。fail-fast 天然无 attempt
      // 行（早于 create_attempt return），dbSeed 种一条使读断言有内容；
      // 清理时随 ai_chat_sessions FK ON DELETE CASCADE 级联删除
      dbSeed(`INSERT INTO ai_execution_attempts
                (id, session_id, source_type, attempt_no, operation, status, started_at)
              VALUES ('att-e2e-${Date.now()}', '${sid}', 'batch', 1, 'send', 'failed', NOW())`)
      const att = await fetch(`${API}/ai/chat/admin/batches/sessions/${sid}/attempt-timeline`,
        { headers: authHeaders(admin) })
      expect(att.status).toBeLessThan(300)
      const body = await att.json()
      expect(body.sessionId).toBe(sid)
      expect(Array.isArray(body.attempts)).toBe(true)
      expect(body.attempts.length).toBeGreaterThanOrEqual(1)
      expect(body.attempts[0].operation).toBe('send')
      expect(body.attempts[0].status).toBe('failed')

      // 404 通道：不存在的会话
      const nf = await fetch(`${API}/ai/chat/admin/batches/sessions/ses-no-such/attempt-timeline`,
        { headers: authHeaders(admin) })
      expect(nf.status).toBe(404)
    } finally { await cleanupBatch(admin, bid) }
  })

  test('AdminBatchFiles 文件面：uploads 分组可见、download、导入 data_files 幂等', async () => {
    const admin = await adminTokenCached()
    const bid = await failFastBatch(admin, { files: 1 })
    try {
      await waitFor(async () => (await getDetail(admin, bid)).batch.status === 'failed',
        90_000, 'failed')
      const sid = (await getDetail(admin, bid)).sessions[0].id

      // 文件清单：{files:[{name,path,dir,size}], truncated}；uploads 输入可见
      const fres = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/files`,
        { headers: authHeaders(admin) })
      expect(fres.status).toBeLessThan(300)
      const filesBody = await fres.json()
      const all: any[] = filesBody.files ?? []
      expect(typeof filesBody.truncated).toBe('boolean')
      const upload = all.find((f: any) => f.dir === 'uploads' && f.path.includes('in-0'))
      expect(upload, `uploads 分组缺 in-0: ${JSON.stringify(all)}`).toBeTruthy()
      expect(upload.path).toBe('uploads/in-0.txt')

      // 下载端点可达且内容为上传原文（send_file as_attachment）
      const dl = await fetch(
        `${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/files/download?path=${encodeURIComponent(upload.path)}`,
        { headers: authHeaders(admin) })
      expect(dl.status).toBeLessThan(300)
      expect(await dl.text()).toContain('e2e 输入 0')

      // 导入：POST {paths:[...]} → results[].status ∈ imported|existing（幂等）
      const importOnce = () => fetch(
        `${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/files/import`,
        { method: 'POST', headers: authHeaders(admin),
          body: JSON.stringify({ paths: [upload.path] }) })
      const first = await (await importOnce()).json()
      expect(first.results?.[0]?.status).toMatch(/imported|existing/)
      expect(first.results[0].file?.id).toBeTruthy()
      const second = await (await importOnce()).json()
      expect(second.results?.[0]?.status).toBe('existing')
    } finally { await cleanupBatch(admin, bid) }
  })
})

test.describe('admin UI 面', () => {
  test('列表→详情抽屉→重试全部失败→软删消失（fail-fast 驱动，0 LLM）', async ({ page }) => {
    const admin = await adminTokenCached()
    const name = tag('admui')
    const bid = await failFastBatch(admin, { files: 1, name })
    try {
      await waitFor(async () => (await getDetail(admin, bid)).batch.status === 'failed',
        90_000, '第一轮 failed')

      // 列表：?tab=batches 落「批量执行」tab，最新批在第一页（created_at DESC）。
      // getByRole 无 hasText 选项——行定位用可访问名子串匹配（name 选项）
      await gotoWithAuth(page, '/admin/ai-execution?tab=batches')
      const row = page.getByRole('row', { name }).first()
      await row.waitFor({ state: 'visible', timeout: 30_000 })
      await screenshot(page, 'admin-batch-list')

      // 详情抽屉：行内「详情」link button（AiBatchAdmin.vue:51）。操作列
      // fixed="right" —— Element Plus 会克隆一份固定列表格，行/按钮在 DOM 各出现
      // 两次，所有行内定位取 .first() 消解严格模式冲突
      await row.getByRole('button', { name: '详情' }).first().click()
      const drawer = page.locator('.el-drawer')
      await expect(drawer).toBeVisible({ timeout: 15_000 })
      // 子任务行：文件列 in-0.txt + 状态「失败」
      const childRow = drawer.locator('.el-table__row', { hasText: 'in-0' }).first()
      await expect(childRow).toBeVisible({ timeout: 15_000 })
      await expect(childRow).toContainText('失败')
      await screenshot(page, 'admin-batch-drawer')

      // 重试全部失败（:73）→ 成功 toast「已重置 1 个失败的子任务」（:300）
      await drawer.getByRole('button', { name: /重试全部失败/ }).click()
      await expect(page.locator('.el-message', { hasText: '已重置 1 个失败' }).first())
        .toBeVisible({ timeout: 10_000 })
      await screenshot(page, 'admin-batch-retry')

      // 第二轮 fail-fast 收敛（API 侧权威断言；抽屉明细不自动轮询）
      await waitBatchTerminal(admin, bid, 120_000, 2000)

      // 重开抽屉刷新子任务行 → 终态「删除」可用（isTerminal）
      await page.keyboard.press('Escape')
      await expect(drawer).not.toBeVisible({ timeout: 10_000 })
      await row.getByRole('button', { name: '详情' }).first().click()
      await expect(drawer).toBeVisible({ timeout: 15_000 })
      await expect(drawer.locator('.el-table__row', { hasText: 'in-0' }).first())
        .toContainText('失败')

      // 软删：子任务行「删除」（:94）→ ElMessageBox 确认「确定删除?」（:313-315）
      await drawer.locator('.el-table__row', { hasText: 'in-0' }).first()
        .getByRole('button', { name: '删除' }).click()
      const box = page.locator('.el-message-box')
      await expect(box).toBeVisible({ timeout: 10_000 })
      await expect(box).toContainText('确定删除')
      await box.locator('.el-button--primary').click()
      // 成功 toast「已软删除(原状态:…)」+ 行从抽屉消失（detail 重取，
      // deleted_at IS NULL 过滤——与用例 2 的 API 断言同源）
      await expect(page.locator('.el-message', { hasText: '已软删除' }))
        .toBeVisible({ timeout: 10_000 })
      await expect(drawer.locator('.el-table__row', { hasText: 'in-0' }))
        .toHaveCount(0, { timeout: 15_000 })
      await screenshot(page, 'admin-batch-softdel')
    } finally { await cleanupBatch(admin, bid) }
  })
})
