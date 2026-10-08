/**
 * 批子会话 SSE 流式回归（2026-10-08 缺陷修复）。
 *
 * 批子会话由 worker 驱动、浏览器中途打开；OpenCode 事件不回放，订阅前已
 * 发出的 message.updated 错过后，_assistantMsgIds 门会把该回合全部 part
 * 事件丢弃——subagent 委派区块/文本全部不渲染（但任务照常运行）。修复：
 * 后端在 SSE 订阅起点按 REST 快照补发一爆发（message.updated +
 * message.part.updated，带 snapshot 标记），前端把已持久化的同名消息行
 * 收编为流式目标（清空重建、part 索引对齐真实 part id）并置 streaming，
 * 让轮询守卫按「回合进行中」行事；重连时允许轮询降级并丢弃流式状态。
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
  getChanges: vi.fn(),
  getCommands: vi.fn(() => Promise.resolve({ commands: [], skills: [] })),
  getRuntimeState: vi.fn(),
  createEventStream: vi.fn(() => ({ close: vi.fn() })),
}))

import { useAiChatStore } from '../aiChat'
import * as api from '@/api/aiChat'

const SID = 'batch_child_1'

/** worker 已持久化的进行中快照：user 行 + assistant 行（扁平化内容，无 part id） */
const PERSISTED_MIDRUN = {
  messages: [
    { id: 'u1', role: 'user', seq: 1, content: [{ type: 'text', text: '任务指令' }] },
    { id: 'm_midrun', role: 'assistant', seq: 2,
      content: [{ type: 'subtask_use', subtaskId: 'oc_child_1', agent: 'general-purpose',
                  description: '分析数据', status: 'running' }] },
  ],
}

/** 订阅起点的快照爆发（后端 _midturn_snapshot_events 产出，同构于实时事件） */
function emitSnapshotBurst(h: any) {
  h.onEvent({
    event: 'message.updated',
    data: { info: { id: 'm_midrun', role: 'assistant', sessionID: 'oc_root' },
            snapshot: true },
  })
  h.onEvent({
    event: 'message.part.updated',
    data: { snapshot: true, part: {
      id: 'ptask', type: 'tool', tool: 'task', messageID: 'm_midrun',
      sessionID: 'oc_root',
      state: { status: 'running',
               input: { description: '分析数据', subagent_type: 'general-purpose' },
               metadata: { sessionId: 'oc_child_1' } },
    } },
  })
  h.onEvent({
    event: 'message.part.updated',
    data: { snapshot: true, part: {
      id: 'ptext', type: 'text', messageID: 'm_midrun', sessionID: 'oc_root',
      text: '已收到任务，开始处理。',
    } },
  })
}

async function openBatchChild() {
  const ref: { h: any } = { h: null }
  vi.mocked(api.getMessages).mockResolvedValue(PERSISTED_MIDRUN as any)
  vi.mocked(api.createEventStream).mockImplementation((_id: string, h: any) => {
    ref.h = h
    return { close: vi.fn() }
  })
  const store = useAiChatStore()
  await store.openSession(SID)
  ref.h.onStatus?.('open')
  return { store, ref }
}

describe('批子会话 SSE 流式（快照收编流式目标）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('中途挂流：快照爆发收编持久化行，subagent 区块与文本渲染且后续实时事件就地更新', async () => {
    const { store, ref } = await openBatchChild()
    emitSnapshotBurst(ref.h)

    const msgs = store.messages[SID]!
    // 持久化行被收编为流式目标（同 id 重建），不追加第三条幽灵消息
    expect(msgs.map((m: any) => m.id)).toEqual(['u1', 'm_midrun'])
    // streaming 置位：轮询守卫按「回合进行中」行事
    expect(store.streaming[SID]).toBe(true)

    const target = msgs[1] as any
    const sub = target.content.find((p: any) => p.type === 'subtask_use')
    expect(sub).toMatchObject({ subtaskId: 'oc_child_1', agent: 'general-purpose' })
    expect(target.content.find((p: any) => p.type === 'text')?.text)
      .toBe('已收到任务，开始处理。')

    // 订阅后的实时事件（无 snapshot 标记）不再被门丢弃：
    ref.h.onEvent({
      event: 'message.part.updated',
      data: { part: { id: 'ptask', type: 'tool', tool: 'task', messageID: 'm_midrun',
                      sessionID: 'oc_root',
                      state: { status: 'completed',
                               input: { description: '分析数据', subagent_type: 'general-purpose' },
                               metadata: { sessionId: 'oc_child_1' } } } },
    })
    ref.h.onEvent({
      event: 'message.part.delta',
      data: { messageID: 'm_midrun', partID: 'ptext', field: 'text', delta: ' 继续执行。' },
    })
    expect(target.content).toHaveLength(2)  // 就地更新，不重复追加
    expect(target.content.find((p: any) => p.type === 'subtask_use')?.status)
      .toBe('completed')
    expect(target.content.find((p: any) => p.type === 'text')?.text)
      .toBe('已收到任务，开始处理。 继续执行。')
  })

  it('持久化行尚未落库（打开早于首个 progress 持久化）：快照按真实消息 id 新建流式目标', async () => {
    const ref: { h: any } = { h: null }
    vi.mocked(api.getMessages).mockResolvedValue({
      messages: [PERSISTED_MIDRUN.messages[0]],
    } as any)
    vi.mocked(api.createEventStream).mockImplementation((_id: string, h: any) => {
      ref.h = h
      return { close: vi.fn() }
    })
    const store = useAiChatStore()
    await store.openSession(SID)
    ref.h.onStatus?.('open')
    emitSnapshotBurst(ref.h)

    const msgs = store.messages[SID]!
    expect(msgs.map((m: any) => m.id)).toEqual(['u1', 'm_midrun'])
    expect(store.streaming[SID]).toBe(true)
    expect((msgs[1] as any).content.some((p: any) => p.type === 'subtask_use')).toBe(true)
  })

  it('流式进行中 reloadMessages 被守卫挡住（不再整替出陈旧索引竞态）', async () => {
    const { store, ref } = await openBatchChild()
    emitSnapshotBurst(ref.h)

    // 模拟「兄弟子会话状态翻转」触发 watch 重拉：服务端快照滞后（只有 user 行）
    vi.mocked(api.getMessages).mockResolvedValue({
      messages: [PERSISTED_MIDRUN.messages[0]],
    } as any)
    await store.reloadMessages(SID)

    const msgs = store.messages[SID]!
    expect(msgs).toHaveLength(2)  // 未被滞后快照整替
    expect((msgs[1] as any).content.some((p: any) => p.type === 'subtask_use')).toBe(true)
    expect(store.streaming[SID]).toBe(true)
  })

  it('断线重连中允许轮询降级：整替后清空流状态，陈旧 live 事件不再写坏 user 气泡', async () => {
    const { store, ref } = await openBatchChild()
    emitSnapshotBurst(ref.h)

    ref.h.onStatus?.('reconnecting')
    vi.mocked(api.getMessages).mockResolvedValue({
      messages: [PERSISTED_MIDRUN.messages[0]],
    } as any)
    await store.reloadMessages(SID)

    // 降级：收敛到滞后持久化快照，流式状态清空（重连后由新快照爆发重新收编）
    expect(store.messages[SID]!).toHaveLength(1)
    expect(store.streaming[SID]).toBe(false)

    // 降级后到达的陈旧 live part 事件（消息 id 已不在门内）被忽略：
    ref.h.onEvent({
      event: 'message.part.updated',
      data: { part: { id: 'ptask', type: 'tool', tool: 'task', messageID: 'm_midrun',
                      sessionID: 'oc_root',
                      state: { status: 'completed', metadata: { sessionId: 'oc_child_1' } } } },
    })
    const msgs = store.messages[SID]!
    expect(msgs).toHaveLength(1)
    const user = msgs[0] as any
    expect(user.role).toBe('user')
    expect(user.content).toHaveLength(1)          // 提问文本未被顶掉
    expect(user.content[0].type).toBe('text')
  })

  it('流式目标行被外部清空时，live part 落到重建的 assistant 消息（不崩溃/不写 user 气泡）', async () => {
    const ref: { h: any } = { h: null }
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] } as any)
    vi.mocked(api.createEventStream).mockImplementation((_id: string, h: any) => {
      ref.h = h
      return { close: vi.fn() }
    })
    const store = useAiChatStore()
    await store.openSession(SID)
    ref.h.onEvent({ event: 'message.updated', data: { info: { id: 'm2', role: 'assistant' } } })
    ref.h.onEvent({ event: 'message.part.updated',
      data: { part: { id: 'p9', type: 'text', messageID: 'm2', text: '前半' } } })
    expect(store.messages[SID]!).toHaveLength(1)

    // 外部整替（如重新执行清空）把目标行拿掉
    store.messages[SID] = [{ id: 'u9', role: 'user', content: [{ type: 'text', text: '新任务' }] }] as any

    // 同回合后续 live part：目标行不在 → 按 id 重建 assistant 消息，
    // 而不是写进 list[length-1]（旧缺陷：顶掉 user 提问文本）
    ref.h.onEvent({ event: 'message.part.updated',
      data: { part: { id: 'p10', type: 'text', messageID: 'm2', text: '后半' } } })
    const msgs = store.messages[SID]!
    expect(msgs).toHaveLength(2)
    expect((msgs[0] as any).role).toBe('user')
    expect((msgs[0] as any).content[0].type).toBe('text')
    const rebuilt = msgs[1] as any
    expect(rebuilt.role).toBe('assistant')
    expect(rebuilt.content).toEqual([{ type: 'text', text: '后半' }])
  })

  it('无 message.updated 且无快照的 part 事件仍被忽略（防串扰，行为不变）', async () => {
    const ref: { h: any } = { h: null }
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] } as any)
    vi.mocked(api.createEventStream).mockImplementation((_id: string, h: any) => {
      ref.h = h
      return { close: vi.fn() }
    })
    const store = useAiChatStore()
    await store.openSession(SID)
    ref.h.onEvent({
      event: 'message.part.updated',
      data: { part: { id: 'px', type: 'tool', tool: 'task', messageID: 'm_unknown',
                      state: { status: 'running', metadata: { sessionId: 'oc_child_x' } } } },
    })
    expect(store.messages[SID]!).toHaveLength(0)
  })
})
