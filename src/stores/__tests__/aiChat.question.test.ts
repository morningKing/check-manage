import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  getPendingQuestion: vi.fn(),
  replyQuestion: vi.fn(),
  rejectQuestion: vi.fn(),
  // other named exports used by the store module must exist as no-ops
  createSession: vi.fn(), listSessions: vi.fn(), renameSession: vi.fn(),
  deleteSession: vi.fn(), getMessages: vi.fn(), sendMessage: vi.fn(),
  uploadFile: vi.fn(), listFiles: vi.fn(), getChanges: vi.fn(),
  createEventStream: vi.fn(() => ({ close() {} })),
}))

import { getPendingQuestion, replyQuestion, rejectQuestion } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

const QUESTION = {
  id: 'que_1',
  sessionID: 'oc_1',
  questions: [{ question: 'Which color?', header: 'Color', options: [{ label: 'Red', description: 'warm' }, { label: 'Blue', description: 'cool' }] }],
}

describe('pendingQuestion via SSE', () => {
  it('question.asked stores the request', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(store as any)._handleEvent('s1', 'question.asked', QUESTION)
    expect(store.pendingQuestion['s1']).toEqual(QUESTION)
    expect(store.activePendingQuestion).toEqual(QUESTION)
  })

  it('question.replied for the matching requestID clears it', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingQuestion['s1'] = QUESTION
    ;(store as any)._handleEvent('s1', 'question.replied', { sessionID: 'oc_1', requestID: 'que_1', answers: [['Blue']] })
    expect(store.pendingQuestion['s1']).toBeNull()
  })

  it('question.replied for a DIFFERENT requestID is ignored (stale echo / race)', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingQuestion['s1'] = QUESTION
    ;(store as any)._handleEvent('s1', 'question.replied', { sessionID: 'oc_1', requestID: 'que_other', answers: [['x']] })
    expect(store.pendingQuestion['s1']).toEqual(QUESTION)
  })

  it('question.rejected clears the pending question', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingQuestion['s1'] = QUESTION
    ;(store as any)._handleEvent('s1', 'question.rejected', { sessionID: 'oc_1', requestID: 'que_1' })
    expect(store.pendingQuestion['s1']).toBeNull()
  })

  it('session.idle defensively clears any leftover pending question', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.messages['s1'] = []
    store.pendingQuestion['s1'] = QUESTION
    ;(store as any)._handleEvent('s1', 'session.idle', {})
    expect(store.pendingQuestion['s1']).toBeNull()
  })
})

describe('loadPendingQuestion', () => {
  it('adopts the fetched question', async () => {
    const store = useAiChatStore()
    ;(getPendingQuestion as any).mockResolvedValue({ data: QUESTION })
    await store.loadPendingQuestion('s1')
    expect(store.pendingQuestion['s1']).toEqual(QUESTION)
  })

  it('adopts null and does not throw when the API errors', async () => {
    const store = useAiChatStore()
    ;(getPendingQuestion as any).mockRejectedValue(new Error('boom'))
    await expect(store.loadPendingQuestion('s1')).resolves.toBeUndefined()
    expect(store.pendingQuestion['s1']).toBeUndefined()
  })
})

describe('answerPendingQuestion / rejectPendingQuestion', () => {
  it('answerPendingQuestion posts the answers and optimistically clears', async () => {
    const store = useAiChatStore()
    store.pendingQuestion['s1'] = QUESTION
    ;(replyQuestion as any).mockResolvedValue({ ok: true })
    await store.answerPendingQuestion('s1', [['Blue']])
    expect(replyQuestion).toHaveBeenCalledWith('s1', 'que_1', [['Blue']])
    expect(store.pendingQuestion['s1']).toBeNull()
  })

  it('answerPendingQuestion is a no-op when nothing is pending', async () => {
    const store = useAiChatStore()
    await store.answerPendingQuestion('s1', [['Blue']])
    expect(replyQuestion).not.toHaveBeenCalled()
  })

  it('rejectPendingQuestion posts the rejection and optimistically clears', async () => {
    const store = useAiChatStore()
    store.pendingQuestion['s1'] = QUESTION
    ;(rejectQuestion as any).mockResolvedValue({ ok: true })
    await store.rejectPendingQuestion('s1')
    expect(rejectQuestion).toHaveBeenCalledWith('s1', 'que_1')
    expect(store.pendingQuestion['s1']).toBeNull()
  })
})
