import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import BatchGroup from '../BatchGroup.vue'
import type { AiChatBatch, AiChatBatchSession } from '@/types/aiChatBatch'
import * as batchApi from '@/api/aiChatBatches'

vi.mock('@/api/aiChatBatches')
vi.mock('element-plus', async (importOriginal) => {
  const orig = await importOriginal<typeof import('element-plus')>()
  return {
    ...orig,
    ElMessageBox: { confirm: vi.fn().mockResolvedValue(undefined) },
    ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  }
})

beforeAll(() => {
  globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any
})

function batch(over: Partial<AiChatBatch> = {}): AiChatBatch {
  return {
    id: 'b1', user_id: 'u', name: 'AITEST-批', prompt: 'p', template_id: null,
    agent: null, model: null, status: 'running', total: 3, done: 1, failed: 0,
    cancelled: 0, paused: 0, created_at: '', completed_at: null,
    ...over,
  } as AiChatBatch
}

function session(over: Partial<AiChatBatchSession> = {}): AiChatBatchSession {
  return {
    id: 's1', status: 'running', batch_seq: 1, batch_input_file: 'a/b.csv',
    opencode_session_id: null, error_message: null, last_message_preview: null,
    workspace_path: null,
    ...over,
  } as unknown as AiChatBatchSession
}

const stubs = {
  AppendFilesDialog: { template: '<div />' },
  EditBatchConfigDialog: { template: '<div />' },
}

async function mountGroup(b = batch(), sessions: AiChatBatchSession[] = []) {
  vi.mocked(batchApi.getBatch).mockResolvedValue({ batch: b, sessions })
  const w = mount(BatchGroup, {
    props: { batch: b, activeSessionId: null },
    global: { stubs, plugins: [] },
  })
  await flushPromises()
  return w
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('BatchGroup', () => {
  it('renders name, running badge, progress and 默认 agent/model', async () => {
    const w = await mountGroup(batch(), [])
    expect(w.find('.bg-name').text()).toBe('AITEST-批')
    expect(w.find('.badge').text()).toBe('运行中')
    expect(w.find('.bg-meta').text()).toBe('1/3')
    expect(w.find('.bg-am').text()).toBe('默认 · 默认')
  })

  it('shows 暂停/中断 for running and 重试失败 only when failed>0', async () => {
    const w = await mountGroup(batch(), [])
    expect(w.find('[title="暂停全部（可继续）"]').exists()).toBe(true)
    expect(w.find('[title="中断全部（标记取消，之后可继续）"]').exists()).toBe(true)
    expect(w.find('[title="重试失败"]').exists()).toBe(false)

    const w2 = await mountGroup(batch({ status: 'failed', failed: 2 }), [])
    expect(w2.find('[title="暂停全部（可继续）"]').exists()).toBe(false)
    expect(w2.find('[title="重试失败"]').exists()).toBe(true)
    expect(w2.find('.badge').text()).toBe('失败')
  })

  it('completed batch shows neither pause nor stop nor resume', async () => {
    const w = await mountGroup(batch({ status: 'completed', done: 3 }), [])
    expect(w.find('[title="暂停全部（可继续）"]').exists()).toBe(false)
    expect(w.find('[title="中断全部（标记取消，之后可继续）"]').exists()).toBe(false)
    expect(w.find('[title="继续运行（从暂停/中断处继续）"]').exists()).toBe(false)
  })

  it('head click selects batch; second click clears selection', async () => {
    const w = await mountGroup(batch(), [session()])
    await w.find('.batch-group__head').trigger('click')
    await flushPromises()
    expect(batchApi.getBatch).toHaveBeenCalledWith('b1')
    expect(w.find('.batch-group__body').exists()).toBe(true)
    await w.find('.batch-group__head').trigger('click')
    await flushPromises()
    expect(w.find('.batch-group__body').exists()).toBe(false)
  })

  it('expands children sorted by batch_seq desc, basename file names', async () => {
    const w = await mountGroup(batch(), [
      session({ id: 's-old', batch_seq: 1, batch_input_file: 'x/old.csv', status: 'completed' }),
      session({ id: 's-new', batch_seq: 2, batch_input_file: 'y/new.csv', status: 'completed' }),
    ])
    await w.find('.batch-group__head').trigger('click')
    await flushPromises()
    const files = w.findAll('.bg-child__file').map(n => n.text())
    expect(files).toEqual(['new.csv', 'old.csv'])
  })

  it('failed child shows error message with is-error style and reexec affordance', async () => {
    const w = await mountGroup(batch({ status: 'partial', failed: 1 }), [
      session({ id: 's-f', batch_seq: 1, status: 'failed', error_message: 'OpenCode 超时' }),
    ])
    await w.find('.batch-group__head').trigger('click')
    await flushPromises()
    const preview = w.find('.bg-child__preview')
    expect(preview.text()).toBe('OpenCode 超时')
    expect(preview.classes()).toContain('is-error')
    expect(w.find('.bg-child__reexec').exists()).toBe(true)
  })

  it('running child offers cancel; paused child offers resume', async () => {
    const w = await mountGroup(batch(), [
      session({ id: 's-run', batch_seq: 1, status: 'running' }),
      session({ id: 's-pause', batch_seq: 2, status: 'paused' }),
    ])
    await w.find('.batch-group__head').trigger('click')
    await flushPromises()
    expect(w.find('.bg-child__cancel').exists()).toBe(true)
    expect(w.find('.bg-child__resume').exists()).toBe(true)
    expect(w.find('.bg-child__reexec').exists()).toBe(false)
  })

  it('emits selectChild with the session id on child click', async () => {
    const w = await mountGroup(batch(), [session({ id: 's-click' })])
    await w.find('.batch-group__head').trigger('click')
    await flushPromises()
    await w.find('.bg-child').trigger('click')
    expect(w.emitted('selectChild')![0]).toEqual(['s-click'])
  })

  it('delete on non-terminal batch routes through stop=true', async () => {
    const { ElMessageBox } = await import('element-plus')
    const w = await mountGroup(batch({ status: 'running' }), [])
    await w.find('[title="删除批次"]').trigger('click')
    await flushPromises()
    expect(ElMessageBox.confirm).toHaveBeenCalled()
    expect(batchApi.deleteBatch).toHaveBeenCalledWith('b1', true)
  })

  it('delete on terminal batch deletes without stop', async () => {
    const w = await mountGroup(batch({ status: 'completed' }), [])
    await w.find('[title="删除批次"]').trigger('click')
    await flushPromises()
    expect(batchApi.deleteBatch).toHaveBeenCalledWith('b1', false)
  })

  it('retry failed calls retryFailedSessions and refreshes detail', async () => {
    const w = await mountGroup(batch({ failed: 1 }), [])
    vi.mocked(batchApi.retryFailedSessions).mockResolvedValue({ retried: 1 } as any)
    vi.mocked(batchApi.getBatch).mockResolvedValue({
      batch: batch({ failed: 0 }), sessions: [],
    })
    await w.find('[title="重试失败"]').trigger('click')
    await flushPromises()
    expect(batchApi.retryFailedSessions).toHaveBeenCalledWith('b1')
  })
})
