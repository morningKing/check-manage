/**
 * LogViewer 组件单元测试（2026-10-14 spec §6）：轮询/暂停/筛选/轮转/裁剪/切文件。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const mockGet = vi.fn()
vi.mock('@/utils/request', () => ({
  get: (...args: unknown[]) => mockGet(...args),
}))

import LogViewer from '../LogViewer.vue'

function resp(over: Record<string, unknown> = {}) {
  return {
    file: 'chat', offset: 10, size: 10, rotated: false,
    truncated: false, missing: false, lines: [], ...over,
  }
}

async function mountView() {
  const wrapper = mount(LogViewer)
  await flushPromises()
  return wrapper
}

describe('LogViewer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mockGet.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('首次哨兵 offset=-1，随后按返回 offset 增量轮询', async () => {
    mockGet
      .mockResolvedValueOnce(resp({ lines: ['alpha'], offset: 10 }))
      .mockResolvedValueOnce(resp({ lines: ['beta'], offset: 20 }))
    const wrapper = await mountView()
    expect(mockGet).toHaveBeenCalledWith('/ai/logs/tail',
      { file: 'chat', offset: -1, limit: 300 })
    expect(wrapper.text()).toContain('alpha')
    await vi.advanceTimersByTimeAsync(1500)
    await flushPromises()
    expect(mockGet).toHaveBeenLastCalledWith('/ai/logs/tail',
      { file: 'chat', offset: 10, limit: 300 })
    expect(wrapper.text()).toContain('beta')
    wrapper.unmount()
  })

  it('暂停停表不发请求，继续立即补拉', async () => {
    mockGet.mockResolvedValue(resp({ lines: ['x'] }))
    const wrapper = await mountView()
    const calls = mockGet.mock.calls.length
    await wrapper.find('[data-test="pause-btn"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    expect(mockGet.mock.calls.length).toBe(calls)
    await wrapper.find('[data-test="pause-btn"]').trigger('click')
    await flushPromises()
    expect(mockGet.mock.calls.length).toBe(calls + 1)
    wrapper.unmount()
  })

  it('关键字筛选只渲染命中行并计数', async () => {
    mockGet.mockResolvedValueOnce(resp({
      lines: ['gate hit', 'other line', 'gate again'], offset: 30,
    }))
    const wrapper = await mountView()
    await wrapper.find('[data-test="filter-input"] input').setValue('gate')
    await flushPromises()
    expect(wrapper.text()).toContain('gate hit')
    expect(wrapper.text()).not.toContain('other line')
    expect(wrapper.text()).toContain('命中 2 / 共 3')
    wrapper.unmount()
  })

  it('rotated 响应清缓冲并插分隔线', async () => {
    mockGet
      .mockResolvedValueOnce(resp({ lines: ['old1'], offset: 999 }))
      .mockResolvedValueOnce(resp({ rotated: true, lines: ['new1'], offset: 5 }))
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('old1')
    await vi.advanceTimersByTimeAsync(1500)
    await flushPromises()
    expect(wrapper.text()).toContain('文件已轮转')
    expect(wrapper.text()).not.toContain('old1')
    expect(wrapper.text()).toContain('new1')
    wrapper.unmount()
  })

  it('超出缓冲上限裁剪最早行并显示标记', async () => {
    const many = Array.from({ length: 5200 },
      (_, i) => `#${String(i).padStart(4, '0')}#`)
    mockGet.mockResolvedValueOnce(resp({ lines: many, offset: 1 }))
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('已裁剪更早日志')
    expect(wrapper.text()).toContain('#5199#')
    expect(wrapper.text()).not.toContain('#0999#')
    expect(wrapper.text()).toContain('#1000#')
    wrapper.unmount()
  })

  it('文件切换重置 offset 并请求新文件', async () => {
    mockGet.mockResolvedValue(resp({ lines: [] }))
    const wrapper = await mountView()
    // 广播组件事件直驱 v-model + change（jsdom 里 radio label 点击不可靠，
    // 真实浏览器行为由 E2E 覆盖）
    const group = wrapper.findComponent({ name: 'ElRadioGroup' })
    group.vm.$emit('update:modelValue', 'serve')
    group.vm.$emit('change', 'serve')
    await flushPromises()
    expect(mockGet).toHaveBeenLastCalledWith('/ai/logs/tail',
      { file: 'serve', offset: -1, limit: 300 })
    wrapper.unmount()
  })

  it('missing 响应显示等待文案', async () => {
    mockGet.mockResolvedValueOnce(resp({ missing: true, lines: [] }))
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('暂无日志文件（等待产生）')
    expect(mockGet).toHaveBeenLastCalledWith('/ai/logs/tail',
      { file: 'chat', offset: -1, limit: 300 })
    wrapper.unmount()
  })
})
