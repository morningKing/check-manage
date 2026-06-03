import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, it, expect, vi } from 'vitest'

vi.mock('@/api/aiScanTask', () => ({
  getScanTasks: vi.fn(() => Promise.resolve([{ id: 't1', name: 'n' }])),
  getScanTask: vi.fn(), createScanTask: vi.fn(() => Promise.resolve({ id: 't2' })),
  updateScanTask: vi.fn(() => Promise.resolve({})), deleteScanTask: vi.fn(() => Promise.resolve()),
  runScanTaskNow: vi.fn(() => Promise.resolve({ message: 'ok' })),
}))
import { useAiScanTaskStore } from '@/stores/aiScanTask'

describe('aiScanTask store', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('loads tasks', async () => {
    const s = useAiScanTaskStore()
    await s.load()
    expect(s.tasks).toHaveLength(1)
    expect(s.tasks[0].id).toBe('t1')
  })
})
