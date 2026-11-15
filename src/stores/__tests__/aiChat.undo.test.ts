/**
 * aiChat store undo/redo 测试（undo-redo spec Task 11）：
 * - undoFromMessage 以服务端权威窗口为准，失败即不裁剪；
 * - redoSession 恢复窗口并清撤销态；
 * - 发送/命令成功后清撤销态（重做窗口关闭）；
 * - undoLastTurn 把最后一轮文本装回 composerRefill；
 * - session.revert 事件防抖后强一致重载（专用路径采纳变短列表）。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  // 工厂对象与 aiChat.test.ts 头部的 mock 工厂一致，另加 undo/redo 三项——
  // mock 工厂必须覆盖 store 顶层 import 且被调用路径触达的每个名字。
  createSession: vi.fn(),
  listSessions: vi.fn(),
  getRunningSessions: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  clearSession: vi.fn(),
  getMessages: vi.fn(),
  getTurns: vi.fn(async () => ({ turns: [] })),
  sendMessage: vi.fn(),
  setMessageFeedback: vi.fn(),
  uploadFile: vi.fn(),
  listFiles: vi.fn(() => Promise.resolve({ files: [] })),
  getChanges: vi.fn(),
  setPermissionMode: vi.fn(),
  getPendingPermission: vi.fn(),
  createEventStream: vi.fn(() => ({ close: vi.fn() })),
  undoPreview: vi.fn(),
  undoToMessage: vi.fn(),
  redoSession: vi.fn(),
}))

import { useAiChatStore } from '../aiChat'
import { undoToMessage, redoSession, getMessages, sendMessage } from '@/api/aiChat'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('aiChat store undo/redo', () => {
  it('undoFromMessage 采用服务端窗口并记录撤销态，失败时不裁剪', async () => {
    const s = useAiChatStore()
    s.activeSessionId = 's1'
    s.messages['s1'] = [
      { id: 'u1', role: 'user', content: [] },
      { id: 'a1', role: 'assistant', content: [] },
      { id: 'u2', role: 'user', content: [] },
    ] as any
    vi.mocked(undoToMessage).mockRejectedValueOnce(
      { response: { data: { error: { code: 'SESSION_BUSY', message: 'x' } } } })
    await expect(s.undoFromMessage('s1', 'u2')).rejects.toBeTruthy()
    expect(s.messages['s1']).toHaveLength(3)           // 失败不裁剪
    // mock 形态 = 真实载荷（request 拦截器已解包 axios data，顶层即响应体）
    vi.mocked(undoToMessage).mockResolvedValueOnce({
      deleted: 2, revert: { mode: 'native', messageCount: 2, fileCount: 1 },
      messages: [{ id: 'u1', role: 'user', content: [], seq: 2 }],
      hasMore: true,
    } as any)
    await s.undoFromMessage('s1', 'u2')
    expect(s.messages['s1']).toHaveLength(1)
    expect(s.revertState['s1']).toEqual({ mode: 'native', messageCount: 2, fileCount: 1 })
    // 撤销后窗口按服务端权威重算：分页游标同步校正（批次 B1，终审 M1）
    expect(s.hasMoreMessages['s1']).toBe(true)
    expect(s.oldestLoadedSeq['s1']).toBe(2)
  })

  it('deleteFromMessage 委托 undoFromMessage（失败上抛）', async () => {
    const s = useAiChatStore()
    vi.mocked(undoToMessage).mockRejectedValueOnce(new Error('boom'))
    await expect(s.deleteFromMessage('s1', 'u2')).rejects.toBeTruthy()
  })

  it('redoSession 恢复窗口并清撤销态，同步分页游标', async () => {
    const s = useAiChatStore()
    s.revertState['s1'] = { mode: 'native', messageCount: 2, fileCount: 0 }
    vi.mocked(redoSession).mockResolvedValueOnce({
      deleted: 2, messages: [{ id: 'u1', role: 'user', content: [], seq: 1 },
                             { id: 'a1', role: 'assistant', content: [], seq: 2 }],
      hasMore: false,
    } as any)
    await s.redoSession('s1')
    expect(s.messages['s1']).toHaveLength(2)
    expect(s.revertState['s1']).toBeNull()
    // 重做恢复同理：游标按恢复后的窗口校正（批次 B1）
    expect(s.hasMoreMessages['s1']).toBe(false)
    expect(s.oldestLoadedSeq['s1']).toBe(1)
  })

  it('发送成功清撤销态（重做窗口关闭）', async () => {
    const s = useAiChatStore()
    s.activeSessionId = 's1'
    s.revertState['s1'] = { mode: 'native', messageCount: 2, fileCount: 0 }
    s.streaming['s1'] = false
    vi.mocked(sendMessage).mockResolvedValueOnce({ messageId: 'm1', model: null } as any)
    s.messages['s1'] = []
    await s.sendUserMessage('hi')
    expect(s.revertState['s1']).toBeNull()
  })

  it('undoLastTurn 撤销最后一轮并把文本装回 composerRefill', async () => {
    const s = useAiChatStore()
    s.activeSessionId = 's1'
    s.messages['s1'] = [
      { id: 'u1', role: 'user', content: [{ type: 'text', text: '第一问' }] },
      { id: 'a1', role: 'assistant', content: [] },
      { id: 'u2', role: 'user', content: [{ type: 'text', text: '第二问' }] },
    ] as any
    vi.mocked(undoToMessage).mockResolvedValueOnce({
      deleted: 2, revert: null, messages: [{ id: 'u1', role: 'user', content: [] }],
    } as any)
    const text = await s.undoLastTurn()
    expect(text).toBe('第二问')
    expect(s.composerRefill['s1']).toBe('第二问')
  })

  it('session.revert 事件防抖后强一致重载（采纳变短列表）', async () => {
    vi.useFakeTimers()
    try {
      const s = useAiChatStore()
      s.activeSessionId = 's1'
      s.messages['s1'] = [{ id: 'u1', role: 'user', content: [] },
                          { id: 'a1', role: 'assistant', content: [] }] as any
      vi.mocked(getMessages).mockResolvedValueOnce({
        messages: [{ id: 'u1', role: 'user', content: [] }],
        revert: { mode: 'native', messageCount: 1, fileCount: 0 },
      } as any)
      s._handleEvent('s1', 'session.revert', { sessionId: 's1', revert: null })
      await vi.advanceTimersByTimeAsync(400)
      expect(s.messages['s1']).toHaveLength(1)           // 变短被采纳
      expect(s.revertState['s1']!.messageCount).toBe(1)
    } finally {
      // 测试卫生（批次 B5/T11）：中途断言失败也不向同文件后续用例泄漏假计时器
      vi.useRealTimers()
    }
  })
})
