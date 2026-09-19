import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import MemoryManager from '../MemoryManager.vue'
import * as chatApi from '@/api/aiChat'

vi.mock('@/api/aiChat')
vi.mock('element-plus', async (importOriginal) => {
  const orig = await importOriginal<typeof import('element-plus')>()
  return {
    ...orig,
    ElMessageBox: { confirm: vi.fn().mockResolvedValue(undefined) },
    ElMessage: { success: vi.fn(), error: vi.fn() },
  }
})

beforeAll(() => {
  globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any
})

const mem = (over: Record<string, unknown>) =>
  ({ id: 'm1', memory: '负责 PostgreSQL 运维', ...over }) as any

// 让 ElDrawer 桩挂载时触发 open → 组件 load()
const drawerStub = {
  template: '<div><slot /></div>',
  props: ['modelValue', 'title', 'size'],
  emits: ['open', 'update:modelValue'],
  mounted() { this.$emit('open') },
}

const stubs = {
  'el-drawer': drawerStub,
  'el-switch': {
    template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
    props: ['modelValue'], emits: ['update:modelValue'],
  },
}

beforeEach(() => {
  vi.clearAllMocks()
})

function mountDrawer() {
  return mount(MemoryManager, { props: { modelValue: true }, global: { stubs } })
}

describe('MemoryManager', () => {
  it('loads memories on open and renders source labels', async () => {
    vi.mocked(chatApi.listMemories).mockResolvedValue({
      memories: [
        mem({ id: 'a', metadata: { source: 'manual' } }),
        mem({ id: 'b', metadata: { source: 'batch' }, memory: '偏好英文命名' }),
        mem({ id: 'c', memory: '老条目' }),   // 无来源 → 系统记忆
      ],
    } as any)
    const w = mountDrawer()
    await flushPromises()
    const labels = w.findAll('.mem-source').map(n => n.text())
    expect(labels).toEqual(['手动添加', '批任务自动提取', '系统记忆'])
    expect(w.findAll('.mem-list li').length).toBe(3)
  })

  it('load failure renders error state, not empty (P2 §7.6)', async () => {
    vi.mocked(chatApi.listMemories).mockRejectedValue(new Error('boom'))
    const w = mountDrawer()
    await flushPromises()
    expect(w.find('[data-test="mem-error"]').exists()).toBe(true)
    expect(w.text()).not.toContain('暂无长期记忆')
    vi.mocked(chatApi.listMemories).mockResolvedValue({ memories: [mem({})] } as any)
    await w.find('[data-test="mem-retry"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="mem-error"]').exists()).toBe(false)
  })

  it('search filter narrows the list', async () => {
    vi.mocked(chatApi.listMemories).mockResolvedValue({
      memories: [mem({ id: 'a', memory: 'PostgreSQL 运维' }),
                 mem({ id: 'b', memory: '喜欢英文命名' })],
    } as any)
    const w = mountDrawer()
    await flushPromises()
    await w.find('input[data-test="mem-search"]').setValue('PostgreSQL')
    await flushPromises()
    expect(w.findAll('.mem-list li').length).toBe(1)
  })

  it('add button disabled while draft empty; addMemory called with verbatim flag', async () => {
    vi.mocked(chatApi.listMemories).mockResolvedValue({ memories: [] } as any)
    vi.mocked(chatApi.addMemory).mockResolvedValue({ memories: [mem({})] } as any)
    const w = mountDrawer()
    await flushPromises()
    const btn = () => w.findAll('button').find(b => b.text().includes('添加'))!
    expect((btn().element as HTMLButtonElement).disabled).toBe(true)
    await w.find('textarea').setValue('关键事实')
    expect((btn().element as HTMLButtonElement).disabled).toBe(false)
    // 默认 AI 提炼（verbatim=false）
    await btn().trigger('click')
    await flushPromises()
    expect(chatApi.addMemory).toHaveBeenCalledWith('关键事实', false)
  })

  it('delete asks confirm then removes the item', async () => {
    vi.mocked(chatApi.listMemories).mockResolvedValue({
      memories: [mem({ id: 'del-1' }), mem({ id: 'keep' })],
    } as any)
    vi.mocked(chatApi.deleteMemory).mockResolvedValue({} as any)
    const w = mountDrawer()
    await flushPromises()
    const delBtn = w.findAll('button').find(b => b.text().includes('删除'))!
    await delBtn.trigger('click')
    await flushPromises()
    expect(chatApi.deleteMemory).toHaveBeenCalledWith('del-1')
    const ids = w.findAll('.mem-list li').map(li => li.attributes('data-mem-id'))
    expect(ids).toEqual(['keep'])
  })

  it('delete without confirm does nothing (cancel path)', async () => {
    const { ElMessageBox } = await import('element-plus')
    vi.mocked(ElMessageBox.confirm).mockRejectedValue('cancel')
    vi.mocked(chatApi.listMemories).mockResolvedValue({ memories: [mem({ id: 'x' })] } as any)
    const w = mountDrawer()
    await flushPromises()
    const delBtn = w.findAll('button').find(b => b.text().includes('删除'))!
    await delBtn.trigger('click')
    await flushPromises()
    expect(chatApi.deleteMemory).not.toHaveBeenCalled()
    expect(w.findAll('.mem-list li').length).toBe(1)
  })
})
