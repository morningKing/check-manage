/**
 * SkillOpt 拟合链路功能正确性 E2E（spec §3，TC-FIT-01~06）。
 * apply/preview 纯机械（steps 由请求体传入，不经 LLM）→ 确定性覆盖；
 * generate/diagnose 打 LLM → @llm 冒烟（TC-FIT-06）。
 */
import { test, expect } from '@playwright/test'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { api, gotoWithAuth } from './helpers'
import { API, dbSeed } from './batch/toolbox'
import { newId, seedPlainSession, cleanupSessionsByPrefix } from './db-helpers'
import { seedFitAttempt, cleanupFitSeeds, TWO_STEPS, resolveWorkspaceRoot } from './fit-seed'

test.setTimeout(180_000)

test('TC-FIT-01 拟合三态：recompute 落库 + 幂等不重复 + 明细 perStep + 汇总聚合', async ({ page, request }) => {
  const aFit = await seedFitAttempt(['read', 'write'])       // 2/2 → fit, 100
  const aPart = await seedFitAttempt(['read'])               // 1/2 → partial, 50
  const aDiv = await seedFitAttempt(['glob'])                // 0/2 → diverged, 0
  try {
    for (const [a, status] of [[aFit, 'fit'], [aPart, 'partial'], [aDiv, 'diverged']] as const) {
      const r = await api(request, 'post', `/ai/chat/admin/skill-fit/${a.attemptId}/recompute`)
      expect(r.status).toBe(200)
      expect(r.json.fits[0].status).toBe(status)
    }
    const list = (await api(request, 'get', `/ai/chat/admin/skill-fit?defName=${aFit.defName}&limit=100`)).json
    expect(list.fits).toHaveLength(3)
    expect(list.fits.find((f: any) => f.attemptId === aFit.attemptId).score).toBe(100)
    expect(list.fits.find((f: any) => f.attemptId === aPart.attemptId).score).toBe(50)
    expect(list.fits.find((f: any) => f.attemptId === aDiv.attemptId).score).toBe(0)
    // 幂等：重复 recompute 不产生重复行（uq_skill_fit_attempt_def upsert）
    await api(request, 'post', `/ai/chat/admin/skill-fit/${aFit.attemptId}/recompute`)
    expect(((await api(request, 'get', `/ai/chat/admin/skill-fit?defName=${aFit.defName}&limit=100`)).json.fits)).toHaveLength(3)
    // 明细：partial 的 perStep 状态恰为 hit, miss
    const detail = (await api(request, 'get', `/ai/chat/admin/skill-fit/${aPart.attemptId}`)).json
    expect(detail.fits[0].perStep.map((s: any) => s.status)).toEqual(['hit', 'miss'])
    // definition-summary 聚合 + 拟合时自动登记 def 版本
    const sum = (await api(request, 'get', '/ai/chat/admin/skill-fit/definition-summary')).json
    const d = sum.definitions.find((x: any) => x.defName === aFit.defName)
    expect(d.tasks).toBe(3)
    expect(d.versions).toBeGreaterThanOrEqual(1)
    // 404：attempt 不存在
    expect((await api(request, 'post', `/ai/chat/admin/skill-fit/${newId('att_')}/recompute`)).status).toBe(404)
    // UI：主从布局出现定义行 + 结果行 + 重新计算 toast
    await gotoWithAuth(page, '/admin/ai-skills?tab=fit')
    const defCard = page.locator('.fit-def', { hasText: aFit.defName }).first()
    await defCard.waitFor({ state: 'visible', timeout: 30_000 })
    await defCard.click()
    await expect(page.locator('.el-table__row').first()).toBeVisible({ timeout: 15_000 })
    // 「重新计算」按钮在行展开明细内（FitResultsPanel.vue type=expand 列的
    // .fit-detail__ops），仅展开态渲染——先点首行展开箭头展开明细行
    await page.locator('.el-table__expand-icon').first().click()
    await page.locator('button', { hasText: '重新计算' }).first().click()
    await expect(page.locator('.el-message', { hasText: '已重新计算' })).toBeVisible()
  } finally { cleanupFitSeeds() }
})

test('TC-FIT-02 preview：贪心匹配契约 + 400 形状族 + 404 + 不落库', async ({ request }) => {
  const a = await seedFitAttempt(['read', 'write'])
  try {
    // steps 带 name：匹配引擎 match_steps 直取 s['name']（skill_fit.py:117），
    // 定义解析入口恒补 name（skill_fit.py:63）——preview 入参按解析产物同形；
    // 缺 name 时 preview_steps 自身校验放行但匹配崩（KeyError → 404 'name'）。
    const steps = [
      { id: 'read_input', name: 'read_input', expect: [{ tool: 'read' }] },
      { id: 'save_result', name: 'save_result', expect: [{ tool: 'write', args_pattern: '"path"' }] },
    ]
    const r = await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { attemptId: a.attemptId, steps })
    expect(r.status).toBe(200)
    expect(r.json.preview).toMatchObject({ steps_total: 2, steps_hit: 2, score: 100, status: 'fit' })
    // 不落库：行数不变
    const list = (await api(request, 'get', `/ai/chat/admin/skill-fit?defName=${a.defName}&limit=100`)).json
    expect(list.fits).toHaveLength(0)
    // 400 族：缺 id / expect 非数组 / 非法正则 / 缺 attemptId
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { attemptId: a.attemptId, steps: [{ expect: [{ tool: 'read' }] }] })).status).toBe(400)
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { attemptId: a.attemptId, steps: [{ id: 'x', expect: 'read' }] })).status).toBe(400)
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { attemptId: a.attemptId, steps: [{ id: 'x', expect: [{ tool: 'read', args_pattern: '(unclosed' }] }] })).status).toBe(400)
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { steps })).status).toBe(400)
    // 404：attempt 不存在
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { attemptId: newId('att_'), steps })).status).toBe(404)
  } finally { cleanupFitSeeds() }
})

test('TC-FIT-03 回写→自动归档→回滚：sha256 自洽、虚假版本零产生、越界/非法正则拒绝', async ({ request }) => {
  const a = await seedFitAttempt(['read', 'write'])
  // 产品口径：apply/rollback 的 def_name 由文件路径派生（SKILL.md → 目录名，
  // ai_session_admin.py:1400-1404）；rollback 按 (kind, def_name) 反查最新
  // manifest 定位文件（:1248-1252）。fit-seed 的进程级共享 defName 与 mkdtemp
  // 目录名必然不同、且 fit-seed.ts 冻结不可改——按生产不变式（manifest.name
  // == 定义目录名）把该 attempt 的种子 manifest 改名为路径派生名，版本时间线
  // 按该名观测，断言强度不变。
  const pathDefName = path.basename(path.dirname(a.path))
  dbSeed(`UPDATE ai_execution_manifests SET name='${pathDefName}' WHERE attempt_id='${a.attemptId}' AND kind='skill'`)
  const versionsOf = async () => (await api(request, 'get',
    `/ai/chat/admin/skill-def-versions?defKind=skill&defName=${pathDefName}`)).json.versions
  try {
    // 基线：recompute 登记版本（content_hash = 种子文件 sha256）
    await api(request, 'post', `/ai/chat/admin/skill-fit/${a.attemptId}/recompute`)
    const orig = fs.readFileSync(a.path)
    expect(await versionsOf()).toHaveLength(1)

    const newSteps = [...TWO_STEPS, { id: 'apply_marker_step', expect: [{ tool: 'glob' }] }]
    const r = await api(request, 'post', '/ai/chat/admin/skill-def-steps/apply', { path: a.path, steps: newSteps })
    expect(r.status).toBe(200)
    const after = fs.readFileSync(a.path)
    expect(after.equals(orig)).toBe(false)
    expect(after.toString()).toContain('apply_marker_step')   // steps 写进 frontmatter fit.steps
    const newHash = crypto.createHash('sha256').update(after).digest('hex')
    let vs = await versionsOf()
    expect(vs).toHaveLength(2)                                 // 基线 + apply 新版本
    const applied = vs.find((v: any) => v.contentHash === newHash)
    expect(applied).toBeTruthy()
    expect(applied.archived).toBe(true)                        // bytes 回读归档正文
    expect(applied.versionLabel).toBe(`AI步骤优化 ${new Date().toLocaleDateString('sv-SE')}`)
    // 非法正则 400 且不写盘（先校验后写盘）
    const bad = await api(request, 'post', '/ai/chat/admin/skill-def-steps/apply',
      { path: a.path, steps: [{ id: 'x', expect: [{ tool: 'read', args_pattern: '(unclosed' }] }] })
    expect(bad.status).toBe(400)
    expect(fs.readFileSync(a.path).equals(after)).toBe(true)
    // 路径越界 400（_path_in_allowed_roots）
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/apply',
      { path: 'C:/Windows/system32/evil.md', steps: newSteps })).status).toBe(400)
    // 回滚（产品语义：把「目标版本」归档正文 bytes 原样写回文件——
    // ai_session_admin.py:1232-1267 与 DefVersionTimeline.vue 确认框同义；
    // baseline 版本由 recompute 登记、无归档正文，不可作回滚目标）。故先
    // 第二次 apply 造 v3，再回滚到 v2：文件恢复 v2 归档 bytes 且时间线
    // 不产生虚假回滚版本行（对 brief「文件恢复原文」的等强改写，见报告）。
    const r2 = await api(request, 'post', '/ai/chat/admin/skill-def-steps/apply',
      { path: a.path, steps: [...TWO_STEPS, { id: 'apply_marker_step2', expect: [{ tool: 'glob' }] }] })
    expect(r2.status).toBe(200)
    const v3Bytes = fs.readFileSync(a.path)
    expect(v3Bytes.equals(after)).toBe(false)
    vs = await versionsOf()
    expect(vs).toHaveLength(3)
    // 未归档版本（content=NULL）拒绝回滚——与 UI 回滚按钮置灰同契约
    const baseline = vs.find((v: any) => !v.archived)
    expect(baseline).toBeTruthy()
    expect((await api(request, 'post', `/ai/chat/admin/skill-def-versions/${baseline.id}/rollback`)).status).toBe(400)
    // 回滚到 v2：文件恢复 v2 归档 bytes（sha256 自洽）+ 零虚假版本行
    const rb = await api(request, 'post', `/ai/chat/admin/skill-def-versions/${applied.id}/rollback`)
    expect(rb.status).toBe(200)
    const restored = fs.readFileSync(a.path)
    expect(restored.equals(v3Bytes)).toBe(false)               // 真实写回：不再是 v3 内容
    expect(restored.equals(after)).toBe(true)                  // 恰为 v2 归档 bytes
    expect(crypto.createHash('sha256').update(restored).digest('hex')).toBe(applied.contentHash)
    expect(await versionsOf()).toHaveLength(3)
  } finally { cleanupFitSeeds() }
})
