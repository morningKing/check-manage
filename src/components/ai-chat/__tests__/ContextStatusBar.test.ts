import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ContextStatusBar from '../ContextStatusBar.vue'
import type { SessionUsage } from '@/utils/aiUsage'

const stubs = {
  'el-button': {
    template: '<button class="btn" @click="$emit(\'click\')"><slot /></button>',
    emits: ['click'],
  },
  'el-icon': { template: '<i><slot /></i>' },
}

const usage = (over: Partial<SessionUsage> = {}): SessionUsage => ({
  contextTokens: 12_300,
  totalTokens: 45_600,
  cost: 0.12,
  ...over,
})

describe('ContextStatusBar', () => {
  it('renders model / context / total segments', () => {
    const w = mount(ContextStatusBar, {
      props: { usage: usage(), modelLabel: 'Provider / Model X', contextLimit: 200_000 },
      global: { stubs },
    })
    const text = w.text()
    expect(text).toContain('模型: Provider / Model X')
    expect(text).toContain('上下文: 12.3k / 200.0k（6.2%）')
    expect(text).toContain('累计: 45.6k · $0.12')
    // 水位 <70%：默认色，无危险气泡
    expect(w.find('.ctx-bar__alert').exists()).toBe(false)
    expect(w.find('.ctx-bar__line').classes()).toContain('ctx-bar__line--ok')
  })

  it('colors the context segment at warn level (70%~90%)', () => {
    const w = mount(ContextStatusBar, {
      props: { usage: usage({ contextTokens: 160_000 }), modelLabel: 'm', contextLimit: 200_000 },
      global: { stubs },
    })
    expect(w.find('.ctx-bar__line').classes()).toContain('ctx-bar__line--warn')
    expect(w.find('.ctx-bar__alert').exists()).toBe(false)
  })

  it('shows the compact-suggestion bubble at >=90% and emits compact', async () => {
    const w = mount(ContextStatusBar, {
      props: { usage: usage({ contextTokens: 184_000 }), modelLabel: 'm', contextLimit: 200_000 },
      global: { stubs },
    })
    expect(w.find('.ctx-bar__line').classes()).toContain('ctx-bar__line--danger')
    const alert = w.find('.ctx-bar__alert')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('上下文已达 92%')
    await alert.find('button.btn').trigger('click')   // 立即压缩
    expect(w.emitted('compact')).toHaveLength(1)
  })

  it('「稍后」dismisses the bubble for this crossing', async () => {
    const w = mount(ContextStatusBar, {
      props: { usage: usage({ contextTokens: 184_000 }), modelLabel: 'm', contextLimit: 200_000 },
      global: { stubs },
    })
    const buttons = w.findAll('.ctx-bar__alert button.btn')
    await buttons[1].trigger('click')   // 稍后
    expect(w.find('.ctx-bar__alert').exists()).toBe(false)
    expect(w.find('.ctx-bar__line').classes()).toContain('ctx-bar__line--danger')  // 状态条仍危险色
  })

  it('hides the percentage when the model declares no context limit', () => {
    const w = mount(ContextStatusBar, {
      props: { usage: usage(), modelLabel: 'm', contextLimit: null },
      global: { stubs },
    })
    expect(w.text()).toContain('上下文: 12.3k')
    expect(w.text()).not.toContain('%')
  })

  it('hides the context segment entirely for a fresh session', () => {
    const w = mount(ContextStatusBar, {
      props: { usage: usage({ contextTokens: null, totalTokens: 0, cost: 0 }), modelLabel: 'm', contextLimit: 200_000 },
      global: { stubs },
    })
    expect(w.text()).not.toContain('上下文')
    expect(w.text()).not.toContain('累计')
    expect(w.text()).toContain('模型: m')
  })
})
