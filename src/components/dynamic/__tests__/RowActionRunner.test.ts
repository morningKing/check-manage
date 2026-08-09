import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import RowActionRunner from '../RowActionRunner.vue'
import type { RowActionConfig } from '@/types/rowAction'

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

const runRowAction = vi.fn()
vi.mock('@/api/rowAction', () => ({
  runRowAction: (...args: unknown[]) => runRowAction(...args),
}))

const confirmMock = vi.fn()
vi.mock('element-plus', async (orig) => {
  const actual = await (orig() as Promise<Record<string, unknown>>)
  return {
    ...actual,
    ElMessageBox: { confirm: (...a: unknown[]) => confirmMock(...a) },
    ElMessage: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  }
})

// 只关心 RowActionRunner 怎么调用 DynamicForm 的 validate()/getFormData()（它没有
// v-model，靠 ref 暴露这两个方法），不需要真的渲染表单控件
const validateMock = vi.fn()
const getFormDataMock = vi.fn()
const DynamicFormStub = {
  name: 'DynamicForm',
  template: '<div class="dynamic-form-stub" />',
  methods: {
    validate: (...a: unknown[]) => validateMock(...a),
    getFormData: (...a: unknown[]) => getFormDataMock(...a),
  },
}

function makeAction(over: Partial<RowActionConfig> = {}): RowActionConfig {
  return {
    id: 'ra-1',
    label: '推送外部',
    actionType: 'webhook',
    enabled: true,
    webhookRuleId: 'wh-1',
    statusField: 'syncStatus',
    runningValue: '同步中',
    doneValue: '已同步',
    failedValue: '同步失败',
    ...over,
  }
}

function mountRunner() {
  return mount(RowActionRunner, {
    props: { collection: 'orders' },
    global: { stubs: { teleport: true, DynamicForm: DynamicFormStub } },
  })
}

describe('RowActionRunner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
    runRowAction.mockResolvedValue({ ok: true, status: 'submitted' })
    confirmMock.mockResolvedValue('confirm')
    validateMock.mockResolvedValue(true)
    getFormDataMock.mockReturnValue({})
  })

  it('无 confirmText、无 paramFields 时直接提交', async () => {
    const w = mountRunner()
    await (w.vm as any).run(makeAction(), { id: 'rec-1' })
    await flushPromises()
    expect(confirmMock).not.toHaveBeenCalled()
    expect(runRowAction).toHaveBeenCalledWith('orders', 'rec-1', 'ra-1', {})
  })

  it('配了 confirmText 时先弹确认', async () => {
    const w = mountRunner()
    await (w.vm as any).run(makeAction({ confirmText: '确定要推送吗？' }), { id: 'rec-1' })
    await flushPromises()
    expect(confirmMock).toHaveBeenCalled()
    expect(runRowAction).toHaveBeenCalled()
  })

  it('取消确认时不提交', async () => {
    confirmMock.mockRejectedValue(new Error('cancel'))
    const w = mountRunner()
    await (w.vm as any).run(makeAction({ confirmText: '确定？' }), { id: 'rec-1' })
    await flushPromises()
    expect(runRowAction).not.toHaveBeenCalled()
  })

  it('配了 paramFields 时打开参数对话框而不是直接提交', async () => {
    const w = mountRunner()
    const action = makeAction({
      paramFields: [
        { fieldName: 'reason', label: '原因', controlType: 'textarea' } as any,
      ],
    })
    await (w.vm as any).run(action, { id: 'rec-1' })
    await flushPromises()
    expect(runRowAction).not.toHaveBeenCalled()
    expect((w.vm as any).paramDialogVisible).toBe(true)
  })

  it('参数表单校验失败时不提交，对话框保持打开', async () => {
    validateMock.mockResolvedValue(false)
    const w = mountRunner()
    const action = makeAction({
      paramFields: [
        { fieldName: 'reason', label: '原因', controlType: 'textarea' } as any,
      ],
    })
    await (w.vm as any).run(action, { id: 'rec-1' })
    await flushPromises()
    await (w.vm as any).submitWithParams()
    await flushPromises()
    expect(runRowAction).not.toHaveBeenCalled()
    expect((w.vm as any).paramDialogVisible).toBe(true)
  })

  it('参数表单校验通过时带着表单数据提交', async () => {
    validateMock.mockResolvedValue(true)
    getFormDataMock.mockReturnValue({ reason: '缺料' })
    const w = mountRunner()
    const action = makeAction({
      paramFields: [
        { fieldName: 'reason', label: '原因', controlType: 'textarea' } as any,
      ],
    })
    await (w.vm as any).run(action, { id: 'rec-1' })
    await flushPromises()
    await (w.vm as any).submitWithParams()
    await flushPromises()
    expect(runRowAction).toHaveBeenCalledWith('orders', 'rec-1', 'ra-1', { reason: '缺料' })
    expect((w.vm as any).paramDialogVisible).toBe(false)
  })

  it('status=running 时启动轮询并在超时后停止', async () => {
    vi.useFakeTimers()
    runRowAction.mockResolvedValue({ ok: true, status: 'running' })
    const w = mountRunner()
    await (w.vm as any).run(makeAction(), { id: 'rec-1' })
    await flushPromises()
    expect((w.vm as any).isPolling).toBe(true)
    // 5 分钟上限
    vi.advanceTimersByTime(5 * 60 * 1000 + 5000)
    await flushPromises()
    expect((w.vm as any).isPolling).toBe(false)
  })

  it('status=submitted 时不轮询', async () => {
    const w = mountRunner()
    await (w.vm as any).run(makeAction({ statusField: undefined }), { id: 'rec-1' })
    await flushPromises()
    expect((w.vm as any).isPolling).toBe(false)
  })

  it('卸载时清理定时器', async () => {
    vi.useFakeTimers()
    runRowAction.mockResolvedValue({ ok: true, status: 'running' })
    const w = mountRunner()
    await (w.vm as any).run(makeAction(), { id: 'rec-1' })
    await flushPromises()
    const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval')
    const clearTimeoutSpy = vi.spyOn(globalThis, 'clearTimeout')
    w.unmount()
    // 轮询定时器（clearInterval）和 5 分钟超时定时器（clearTimeout）都要清，
    // 否则组件卸载后超时定时器仍会残留触发（虽然此时组件已销毁、访问不到，
    // 但定时器本身没被清除）
    expect(clearIntervalSpy).toHaveBeenCalled()
    expect(clearTimeoutSpy).toHaveBeenCalled()
  })
})
