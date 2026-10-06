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
