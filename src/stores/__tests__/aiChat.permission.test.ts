import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  getPendingPermission: vi.fn(),
  replyPermission: vi.fn(),
  // other named exports used by the store module must exist as no-ops
  createSession: vi.fn(), listSessions: vi.fn(), renameSession: vi.fn(),
  deleteSession: vi.fn(), getMessages: vi.fn(), sendMessage: vi.fn(),
  getTurns: vi.fn(async () => ({ turns: [] })),
  uploadFile: vi.fn(), listFiles: vi.fn(), getChanges: vi.fn(),
  getPendingQuestion: vi.fn(), replyQuestion: vi.fn(), rejectQuestion: vi.fn(),
  getRuntimeState: vi.fn(),
  createEventStream: vi.fn(() => ({ close() {} })),
}))

import { getPendingPermission, replyPermission, getPendingQuestion } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  ;(getPendingPermission as any).mockResolvedValue({ data: null, items: [] })
  ;(getPendingQuestion as any).mockResolvedValue({ data: null })
})

const PERMISSION = {
  id: 'per_1',
  sessionID: 'oc_1',
  permission: 'external_directory',
  patterns: ['C:/Users/admin/.check-manage/oc-lab/*'],
  metadata: { filepath: 'C:/Users/admin/.check-manage/oc-lab/secret.txt', parentDir: 'C:/Users/admin/.check-manage/oc-lab' },
  always: ['C:/Users/admin/.check-manage/oc-lab/*'],
}
const PERMISSION2 = { ...PERMISSION, id: 'per_2', metadata: { filepath: 'C:/Windows/win.ini' } }

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('pendingPermissions via SSE（队列语义）', () => {
  it('permission.asked 入队；两个并发询问都保留，头部即当前待答', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(store as any)._handleEvent('s1', 'permission.asked', PERMISSION)
    ;(store as any)._handleEvent('s1', 'permission.asked', PERMISSION2)
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION, PERMISSION2])
    expect(store.activePendingPermission).toEqual(PERMISSION)
    expect(store.activePendingPermissionExtra).toBe(1)
  })

  it('同 id 重复事件不重复入队（爆发/重连补发）', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(store as any)._handleEvent('s1', 'permission.asked', PERMISSION)
    ;(store as any)._handleEvent('s1', 'permission.asked', PERMISSION)
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION])
  })

  it('permission.replied 移除对应 id，头部前移到下一个；不同 id 忽略（stale echo/多标签）', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingPermissions['s1'] = [PERMISSION, PERMISSION2]
    ;(store as any)._handleEvent('s1', 'permission.replied', { sessionID: 'oc_1', requestID: 'per_other' })
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION, PERMISSION2])
    ;(store as any)._handleEvent('s1', 'permission.replied', { sessionID: 'oc_1', requestID: 'per_1' })
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION2])
    expect(store.activePendingPermission).toEqual(PERMISSION2)
  })

  it('answerPendingPermission 成功 → 仅移除已答的 id，其余保留', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingPermissions['s1'] = [PERMISSION, PERMISSION2]
    ;(replyPermission as any).mockResolvedValue({ ok: true })
    await store.answerPendingPermission('s1', 'per_1', 'once')
    expect(replyPermission).toHaveBeenCalledWith('s1', 'per_1', 'once')
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION2])
  })

  it('answerPendingPermission 失败 → 请求保留（卡片不消失）且错误上抛（供卡片复位 busy）', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingPermissions['s1'] = [PERMISSION]
    ;(replyPermission as any).mockRejectedValue(new Error('PERMISSION_NOT_FOUND'))
    await expect(store.answerPendingPermission('s1', 'per_1', 'once')).rejects.toThrow()
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION])
  })

  it('answerPendingPermission 对未知 id 是 no-op（stale card）', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingPermissions['s1'] = [PERMISSION]
    await store.answerPendingPermission('s1', 'per_other', 'always')
    expect(replyPermission).not.toHaveBeenCalled()
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION])
  })

  it('loadPendingPermission 用服务端 items 全量覆盖（断线期漏掉的多个询问都能补回）', async () => {
    const store = useAiChatStore()
    ;(getPendingPermission as any).mockResolvedValue({ data: PERMISSION, items: [PERMISSION, PERMISSION2] })
    await store.loadPendingPermission('s1')
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION, PERMISSION2])
  })

  it('loadPendingPermission 与服务端列表竞态：请求期间新到的询问不被覆盖掉', async () => {
    const store = useAiChatStore()
    store.pendingPermissions['s1'] = [PERMISSION]                 // 请求发起前本地已有
    let resolveRest: (v: unknown) => void = () => {}
    ;(getPendingPermission as any).mockReturnValue(new Promise((r) => { resolveRest = r }))
    const loading = store.loadPendingPermission('s1')
    ;(store as any)._handleEvent('s1', 'permission.asked', PERMISSION2)  // 请求进行中到达
    resolveRest({ data: null, items: [] })                        // 服务端此刻已无挂起
    await loading
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION2])  // 旧的不复活，新的不丢
  })

  it('session.idle 不与服务端硬碰：服务端仍挂起 → 保留；已无 → 清空', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingPermissions['s1'] = [PERMISSION]
    ;(getPendingPermission as any).mockResolvedValue({ data: PERMISSION, items: [PERMISSION] })
    ;(store as any)._handleEvent('s1', 'session.idle', {})
    await flush()
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION])   // abort 后仍可应答
    ;(getPendingPermission as any).mockResolvedValue({ data: null, items: [] })
    ;(store as any)._handleEvent('s1', 'session.idle', {})
    await flush()
    expect(store.pendingPermissions['s1']).toEqual([])
  })

  it('SSE 重连后补取挂起询问（断线窗口内到达的 ask 不再永久失联）', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(getPendingPermission as any).mockResolvedValue({ data: PERMISSION, items: [PERMISSION] })
    await (store as any)._syncAfterReconnect('s1')
    expect(getPendingPermission).toHaveBeenCalledWith('s1')
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION])
  })

  it('clearPendingRequests 清空（批子会话守卫：无人值守不允许 ask）', () => {
    const store = useAiChatStore()
    store.pendingPermissions['s1'] = [PERMISSION, PERMISSION2]
    store.clearPendingRequests('s1')
    expect(store.pendingPermissions['s1']).toEqual([])
  })
})

describe('子代理（子会话）发起的授权询问', () => {
  const CHILD_ASK = { ...PERMISSION, id: 'per_child', sessionID: 'oc_child' }

  it('permission.asked 来自子会话同样入队：被阻塞的是父回合（父的 task 工具在等子代理）', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(store as any)._handleEvent('s1', 'session.hello', { sessionID: 'oc_parent' })
    ;(store as any)._handleEvent('s1', 'permission.asked', CHILD_ASK)
    // 子代理来源要标注（卡片显示「来自子代理」，与重水化路径一致）
    expect(store.pendingPermissions['s1']).toEqual([{ ...CHILD_ASK, subtaskAgent: '' }])
    expect(store.activePendingPermission).toEqual({ ...CHILD_ASK, subtaskAgent: '' })
  })

  it('父会话自己的询问不标注子代理来源', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(store as any)._handleEvent('s1', 'session.hello', { sessionID: 'oc_1' })  // = PERMISSION.sessionID
    ;(store as any)._handleEvent('s1', 'permission.asked', PERMISSION)
    expect(store.pendingPermissions['s1']).toEqual([PERMISSION])
  })

  it('permission.replied 来自子会话按 id 移除（多标签/别处应答的 echo）', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(store as any)._handleEvent('s1', 'session.hello', { sessionID: 'oc_parent' })
    ;(store as any)._handleEvent('s1', 'permission.asked', CHILD_ASK)
    ;(store as any)._handleEvent('s1', 'permission.replied', { sessionID: 'oc_child', requestID: 'per_child' })
    expect(store.pendingPermissions['s1']).toEqual([])
  })

  it('子会话的其它事件仍按外部事件丢弃（不污染父流式状态）', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.streaming['s1'] = true
    ;(store as any)._handleEvent('s1', 'session.hello', { sessionID: 'oc_parent' })
    ;(store as any)._handleEvent('s1', 'session.idle', { sessionID: 'oc_child' })
    expect(store.streaming['s1']).toBe(true)
    expect(store.pendingPermissions['s1'] ?? []).toEqual([])
  })

  it('锚点会随新 session.hello 更新：clearSession 换 OC 会话后事件不再被判为外部', () => {
    // 清空会话 → 后端重建 OpenCode 会话（新 id）。旧锚点会把新 hello 当外部
    // 事件丢掉，锚点停在旧 id 后整条流的事件全被过滤 —— 会话看似卡死。
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(store as any)._handleEvent('s1', 'session.hello', { sessionID: 'oc_old' })
    ;(store as any)._handleEvent('s1', 'session.hello', { sessionID: 'oc_new' })
    store.streaming['s1'] = true
    ;(store as any)._handleEvent('s1', 'session.idle', { sessionID: 'oc_new' })
    expect(store.streaming['s1']).toBe(false)   // 新会话的事件按本会话处理
  })
})
