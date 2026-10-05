/**
 * 批任务权限域（多用户隔离），全部确定性：fail-fast 构造（agent 不存在 →
 * 子任务秒级 failed），0 LLM 调用。
 *
 * 状态码均按 handler 实测钉死（见各断言旁注释引用的源码位置）：
 *  - 详情/DELETE/pause/cancel：归属校验先行，跨用户一律 404（不泄漏存在性，
 *    与「批不存在」同响应——防枚举）。
 *  - retry-failed：归属过滤在 SQL scope 内（owner_scope 带 user_id=%s），跨用户
 *    不报错而是 200 {'retried': 0}——钉死该真实行为并断言无副作用。
 *  - /ai/chat/admin/batches：require_permission('admin.ai_chat_admin')，guest 403。
 */
import { test, expect } from '@playwright/test'
import { API, authHeaders, createBatch, getDetail, cleanupBatch, uploadStaging, waitFor } from './batch-helpers'
import { adminTokenCached, failFastBatch, secondUser, tag } from './toolbox'

test.setTimeout(180_000)

test('跨用户隔离：他人批不可见、控制面拒绝、不泄漏存在性', async () => {
  const admin = await adminTokenCached()
  const userB = await secondUser()
  const bid = await failFastBatch(admin, { files: 1 })
  let ownBid = ''
  try {
    // 前提锚点：admin 自己可见——下面的 404/不可见才是「跨用户」而非「不存在」
    expect((await getDetail(admin, bid)).batch.id).toBe(bid)

    // 列表不可见：list_batches 按 user_id 过滤（batch_repo.py:316）
    const list = await (await fetch(`${API}/ai/chat/batches`, { headers: authHeaders(userB.token) })).json()
    expect(JSON.stringify(list)).not.toContain(bid)

    // 详情防枚举：get_batch_detail SQL `WHERE id=%s AND user_id=%s`（batch_repo.py:349），
    // 无行 → detail 路由 `if not body: 404`（routes/ai_chat_batches.py:161-165）——
    // 跨用户与不存在同响应，钉死 404（非 403，避免枚举确认存在性）
    const detail = await fetch(`${API}/ai/chat/batches/${bid}`, { headers: authHeaders(userB.token) })
    expect(detail.status).toBe(404)

    // 等 fail-fast 子任务落 failed（秒级，0 LLM）。这是 retry-failed 断言的
    // 前提：调用时刻库里确有 admin 名下的 failed 子任务——若归属过滤失效，
    // retried 必 >0；前提不成立则断言无意义。
    await waitFor(async () => {
      const d = await getDetail(admin, bid)
      return d.sessions?.[0]?.status === 'failed' ? d : null
    }, 60_000, 'fail-fast 子任务 failed')

    // 控制面拒绝（归属校验先行，跨用户一律 404）：
    //  - DELETE：路由先 get_batch_detail(user_id,...) → None → 404（ai_chat_batches.py:358-366）
    //  - cancel/pause：cancel_batch/pause_batch 首查 `WHERE id=%s AND user_id=%s`，
    //    无行 return None → 路由 404（batch_repo.py:408/465，ai_chat_batches.py:414/431）
    for (const [method, path] of [
      ['POST', 'pause'], ['POST', 'cancel'], ['DELETE', ''],
    ] as const) {
      const r = await fetch(`${API}/ai/chat/batches/${bid}${path ? '/' + path : ''}`, {
        method, headers: authHeaders(userB.token),
      })
      expect(r.status, `${method} /${path || '(删除)'} 跨用户应 404 防枚举`).toBe(404)
    }
    // retry-failed 特例：reset_failed_to_pending 的 owner_scope 含 user_id=%s
    // （batch_repo.py:1026），跨用户匹配 0 条 → 路由回 200 {'retried': 0}
    // （ai_chat_batches.py:465-477）。钉死真实行为：不 5xx、重排 0 条。
    const rf = await fetch(`${API}/ai/chat/batches/${bid}/retry-failed`, {
      method: 'POST', headers: authHeaders(userB.token),
    })
    expect(rf.status).toBe(200)
    expect((await rf.json()).retried).toBe(0)
    // 无副作用：admin 的 failed 子任务未被重排
    expect((await getDetail(admin, bid)).sessions?.[0]?.status).toBe('failed')

    // 对照组：B 自己的批自己可见（guest 建批仅 login_required，可建；fail-fast 0 LLM）
    const up = await uploadStaging(userB.token, 'own.txt', 'x\n', tag('upl'))
    const own = await createBatch(userB.token, {
      name: tag('own'), prompt: 'x', agent: 'e2e-no-such-agent', files: [up],
    } as any)
    ownBid = (own as any).batch?.id ?? (own as any).id
    expect((await getDetail(userB.token, ownBid)).batch.name).toContain('AITEST')
  } finally {
    // 对照组批的清理必须在 userB.cleanup() 之前，且移入 finally：
    // 断言失败时留在 try 里会泄漏批、随后用户被删成无主行
    if (ownBid) await cleanupBatch(userB.token, ownBid).catch(() => {})   // 兜底（已删则为 no-op）
    await cleanupBatch(admin, bid); await userB.cleanup()
  }
})

test('非 admin 访问管理面 403；admin 正常', async () => {
  const userB = await secondUser()
  try {
    // require_permission('admin.ai_chat_admin') → guest 无该能力 → 403「权限不足」
    // （auth.py:116-135）；路由注册见 routes/ai_batch_admin.py:51-53
    const r = await fetch(`${API}/ai/chat/admin/batches`, { headers: authHeaders(userB.token) })
    expect(r.status).toBe(403)
    const admin = await adminTokenCached()
    const ok = await fetch(`${API}/ai/chat/admin/batches`, { headers: authHeaders(admin) })
    expect(ok.status).toBeLessThan(300)
  } finally { await userB.cleanup() }
})
