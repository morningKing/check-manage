/**
 * 子代理事件归属路由（2026-10-11 缺陷修复）。
 *
 * 后端 SSE 代理会把已发现子代理的全部事件（message.updated / part /
 * delta / session.idle / session.error）原样转发给浏览器——事件携带的是
 * OpenCode 内部 sessionID（父/子各不相同），与订阅用的平台会话 id 无关。
 * 旧实现把这些事件一律当父会话事件处理：
 *  - 子代理的 message/part 事件写进主消息流（本该在子代理区块里的内容
 *    跑到区块外，reasoning 还会串进全局思考面板）；
 *  - 子代理的 session.idle 重置父会话流式门禁（_resetStreamState），此后
 *    父回合全部 part 事件被丢弃——切回会话/多段委托场景下区块冻结到
 *    回合结束或中断（用户报告的症状 B）；
 *  - 子代理 session.error 清掉父会话 streaming、渲染顶层错误条。
 * 修复：_handleEvent 入口按事件自身 sessionID 做归属路由（父会话 OC id
 * 由后端订阅起点的 session.hello 事件锚定，runtime-state 兜底），子代理
 * 内容统一走 REST 轮询，一律不触碰父会话流式状态。
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  createSession: vi.fn(),
  listSessions: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  clearSession: vi.fn(),
  getMessages: vi.fn(),
  sendMessage: vi.fn(),
  uploadFile: vi.fn(),
  listFiles: vi.fn(() => Promise.resolve({ files: [] })),
  getChanges: vi.fn(() => Promise.resolve({ changes: [], ok: true })),
  getCommands: vi.fn(() => Promise.resolve({ commands: [], skills: [] })),
  getRuntimeState: vi.fn(),
  getSessionToolCalls: vi.fn(() => Promise.resolve({ calls: [], total: 0 })),
  getPendingQuestion: vi.fn(() => Promise.resolve({ data: null })),
  getPendingPermission: vi.fn(() => Promise.resolve({ data: null })),
  getSessionSkillFit: vi.fn(() => Promise.resolve({ data: null })),
  getSubtaskMessages: vi.fn(() => Promise.resolve({ messages: [] })),
  createEventStream: vi.fn(() => ({ close: vi.fn() })),
}))

import { useAiChatStore } from '../aiChat'
import * as api from '@/api/aiChat'

const SID = 'sess_parent'
const OC = 'oc_root'          // 父会话的 OpenCode 内部 sessionID
const CHILD = 'oc_child_1'    // 子代理的 OpenCode sessionID

/** 订阅起点锚点：后端在爆发/实时事件之前发送（事件归属路由的父会话判据） */
function emitHello(h: any) {
  h.onEvent({ event: 'session.hello', data: { sessionID: OC, sessionId: SID } })
}

/** 父回合进行中（挂流快照形态：streaming 置位 + 收编流式目标）+ task part
 * 宣告委托（前端由此发现子代理 id）。快照形态同时覆盖切回会话与实时回合
 * 两类场景的归属路由。 */
function emitTurnStart(h: any) {
  h.onEvent({
    event: 'message.updated',
    data: { info: { id: 'm1', role: 'assistant', sessionID: OC }, snapshot: true },
  })
  h.onEvent({
    event: 'message.part.updated',
    data: { part: { id: 'ptask', type: 'tool', tool: 'task', messageID: 'm1',
                    sessionID: OC,
                    state: { status: 'running',
                             input: { description: '分析数据',
                                      subagent_type: 'general-purpose' },
                             metadata: { sessionId: CHILD } } } },
  })
}

async function openWithStream(messages: any[] = []) {
  const ref: { h: any } = { h: null }
  vi.mocked(api.getMessages).mockResolvedValue({ messages } as any)
  vi.mocked(api.createEventStream).mockImplementation((_id: string, h: any) => {
    ref.h = h
    return { close: vi.fn() }
  })
  const store = useAiChatStore()
  await store.openSession(SID)
  ref.h.onStatus?.('open')
  return { store, ref }
}

describe('子代理事件归属路由（内容不跑出区块/门禁不被炸）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('子代理事件（含发现前的 message.updated）不进父消息流/思考面板', async () => {
    const { store, ref } = await openWithStream()
    emitHello(ref.h)
    emitTurnStart(ref.h)

    // 子代理自己的消息与正文（sessionID 是子会话）——本该只出现在
    // SubtaskBubble（REST 轮询）里，绝不进主消息流
    ref.h.onEvent({
      event: 'message.updated',
      data: { info: { id: 'cm1', role: 'assistant', sessionID: CHILD } },
    })
    ref.h.onEvent({
      event: 'message.part.updated',
      data: { part: { id: 'cp1', type: 'text', messageID: 'cm1',
                      sessionID: CHILD, text: '子代理内部正文' } },
    })
    ref.h.onEvent({
      event: 'message.part.updated',
      data: { part: { id: 'cr1', type: 'reasoning', messageID: 'cm1',
                      sessionID: CHILD, text: '子代理思考' } },
    })
    // 父正文照常流式
    ref.h.onEvent({
      event: 'message.part.updated',
      data: { part: { id: 'pt1', type: 'text', messageID: 'm1',
                      sessionID: OC, text: '父正文' } },
    })

    const msgs = store.messages[SID]!
    const assistant = msgs.filter((m: any) => m.role === 'assistant')
    expect(assistant).toHaveLength(1)
    const content = assistant[0].content
    expect(content.some((p: any) => p.type === 'subtask_use')).toBe(true)
    expect(content.some((p: any) => p.text === '父正文')).toBe(true)
    // 子代理内容不得出现在主消息流（跑出区块外的旧缺陷）
    expect(content.some((p: any) => p.text === '子代理内部正文')).toBe(false)
    expect(store.reasoning[SID] ?? '').not.toContain('子代理思考')
  })

  it('子代理 session.idle 不重置父会话流式状态：父正文/区块状态继续就地更新', async () => {
    const { store, ref } = await openWithStream()
    emitHello(ref.h)
    emitTurnStart(ref.h)
    ref.h.onEvent({
      event: 'message.part.updated',
      data: { part: { id: 'pt1', type: 'text', messageID: 'm1',
                      sessionID: OC, text: '第一段' } },
    })

    // 子代理先完成（sessionID 是子会话）——旧实现在这里炸掉父门禁
    ref.h.onEvent({ event: 'session.idle', data: { sessionID: CHILD } })

    ref.h.onEvent({
      event: 'message.part.updated',
      data: { part: { id: 'pt1', type: 'text', messageID: 'm1',
                      sessionID: OC, text: '第一段第二段' } },
    })
    ref.h.onEvent({
      event: 'message.part.updated',
      data: { part: { id: 'ptask', type: 'tool', tool: 'task', messageID: 'm1',
                      sessionID: OC,
                      state: { status: 'completed',
                               input: { description: '分析数据' },
                               metadata: { sessionId: CHILD } } } },
    })
    expect(store.streaming[SID]).toBe(true)
    const m = (store.messages[SID] ?? []).find((x: any) => x.role === 'assistant')
    expect(m.content.find((p: any) => p.text?.includes('第二段'))?.text)
      .toBe('第一段第二段')
    expect(m.content.find((p: any) => p.type === 'subtask_use')?.status)
      .toBe('completed')
  })

  it('子代理 session.error 不清父 streaming、不渲染顶层错误条', async () => {
    const { store, ref } = await openWithStream()
    emitHello(ref.h)
    emitTurnStart(ref.h)

    ref.h.onEvent({
      event: 'session.error',
      data: { sessionID: CHILD,
              error: { name: 'UnknownError', data: { message: '子代理失败' } } },
    })
    expect(store.streaming[SID]).toBe(true)
    const m = (store.messages[SID] ?? []).find((x: any) => x.role === 'assistant')
    expect(m?.content.some((p: any) => p.type === 'error')).toBe(false)
  })

  it('message.part.delta 按 messageID 定位目标行（末行是排队气泡也不串写）', async () => {
    const { store, ref } = await openWithStream()
    emitHello(ref.h)
    ref.h.onEvent({
      event: 'message.updated',
      data: { info: { id: 'm1', role: 'assistant', sessionID: OC }, snapshot: true },
    })
    ref.h.onEvent({
      event: 'message.part.updated',
      data: { part: { id: 'pt1', type: 'text', messageID: 'm1',
                      sessionID: OC, text: '第一段' } },
    })
    // 运行中插话的排队气泡排在末行（流式目标行之后）
    ;(store.messages[SID] ?? (store.messages[SID] = [])).push({
      id: 'local_q', role: 'user', content: [{ type: 'text', text: '排队' }],
    } as any)

    ref.h.onEvent({
      event: 'message.part.delta',
      data: { messageID: 'm1', partID: 'pt1', field: 'text', delta: '第二段' },
    })
    const msgs = store.messages[SID]!
    expect(msgs.find((m: any) => m.id === 'm1')?.content[0]?.text)
      .toBe('第一段第二段')
    expect(msgs.find((m: any) => m.id === 'local_q')?.content[0]?.text)
      .toBe('排队')
  })
})

describe('切回会话（新开流）的 runtime-state 对账', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('回合在离开期间结束而爆发缺席：切回后收敛到持久化终态，不再冻结', async () => {
    const ref: { h: any } = { h: null }
    vi.mocked(api.getMessages).mockResolvedValue({
      messages: [
        { id: 'u1', role: 'user', seq: 1,
          content: [{ type: 'text', text: '任务指令' }] },
        { id: 'm_midrun', role: 'assistant', seq: 2,
          content: [{ type: 'text', text: '写到一半' }] },
      ],
    } as any)
    vi.mocked(api.createEventStream).mockImplementation((_id: string, h: any) => {
      ref.h = h
      return { close: vi.fn() }
    })
    const store = useAiChatStore()
    await store.openSession(SID)
    ref.h.onStatus?.('open')
    // 中途挂流的快照爆发（置 streaming + 收编流式目标）
    ref.h.onEvent({
      event: 'message.updated',
      data: { info: { id: 'm_midrun', role: 'assistant', sessionID: OC },
              snapshot: true },
    })
    expect(store.streaming[SID]).toBe(true)

    // 切到别的会话再切回；离开期间回合已结束、最近一条已完成 → 后端不发爆发
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] } as any)
    await store.openSession('sess_other')
    vi.mocked(api.getMessages).mockResolvedValue({
      messages: [
        { id: 'u1', role: 'user', seq: 1,
          content: [{ type: 'text', text: '任务指令' }] },
        { id: 'm_done', role: 'assistant', seq: 2,
          content: [{ type: 'text', text: '最终回答' }] },
      ],
    } as any)
    vi.mocked(api.getRuntimeState).mockResolvedValue({
      sessionId: SID, sessionStatus: 'active', turnStatus: 'idle',
      opencodeSessionId: OC, lastMessageId: 'm_done', error: null,
    } as any)
    await store.openSession(SID)
    // _syncAfterReconnect 是异步兜底：等微任务队列排空
    await new Promise((r) => setTimeout(r, 0))
    await new Promise((r) => setTimeout(r, 0))

    expect(store.streaming[SID]).toBe(false)
    const msgs = store.messages[SID]!
    expect(msgs.some((m: any) => m.id === 'm_done')).toBe(true)
    expect(msgs.some((m: any) => m.id === 'm_midrun')).toBe(false)
  })
})
