import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const mockCreate = vi.fn(); const mockUpdate = vi.fn()
vi.mock('@/api/kefu', () => ({
  createInstance: (...a: any[]) => mockCreate(...a),
  updateInstance: (...a: any[]) => mockUpdate(...a),
}))
vi.mock('@/api/aiChat', () => ({
  listAgents: () => Promise.resolve({ agents: [{ name: 'plan', description: '' }], subagents: [], default: null }),
  listModels: () => Promise.resolve({ models: [{ id: 'p/m', label: 'P / M', providerID: 'p', modelID: 'm', connected: true }], default: '', openCodeDefaults: {} }),
}))
vi.mock('element-plus', () => ({ ElMessage: { success: vi.fn(), error: vi.fn() } }))

import KefuInstanceDialog from '../KefuInstanceDialog.vue'

const stubs = {
  'el-dialog': { template: '<div><slot/><slot name="footer"/></div>', props: ['modelValue', 'title', 'width'] },
  'el-form': { template: '<form><slot/></form>', props: ['label-width'] },
  'el-form-item': { template: '<div><slot/></div>', props: ['label'] },
  'el-input': { props: ['modelValue', 'disabled', 'type', 'rows', 'placeholder'], template: `<input :disabled="disabled" :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />` },
  'el-switch': { props: ['modelValue'], template: `<input type="checkbox" :checked="modelValue" @change="$emit('update:modelValue',$event.target.checked)" />` },
  'el-select': { props: ['modelValue'], template: '<select><slot/></select>' },
  'el-option': { props: ['label', 'value'], template: '<option />' },
  'el-button': { props: ['type', 'disabled', 'loading'], template: `<button :disabled="disabled" @click="$emit('click')"><slot/></button>` },
}
const mountD = (instance: any = null) =>
  mount(KefuInstanceDialog, { props: { modelValue: true, instance }, global: { stubs } })

describe('KefuInstanceDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCreate.mockResolvedValue({ id: 'new1', slug: 'pw', name: 'PW', enabled: true })
    mockUpdate.mockResolvedValue({ id: 'i1', slug: 's', name: 'N', enabled: true })
  })

  it('create mode: slug editable, save calls createInstance with payload', async () => {
    const w = mountD(null)
    await flushPromises()
    expect(w.get('[data-test="slug"]').attributes('disabled')).toBeUndefined()
    ;(w.vm as any).slug = 'pw'; (w.vm as any).name = 'PW'
    await (w.vm as any).save()
    expect(mockCreate).toHaveBeenCalledWith({
      slug: 'pw', name: 'PW', enabled: true, welcome_message: '',
      agent: null, model: null, system_prompt: null, branding: { logo: '' },
    })
    expect(w.emitted('saved')).toBeTruthy()
  })

  it('edit mode: slug disabled + prefilled, updateInstance without slug, preserves branding', async () => {
    const inst = { id: 'i1', slug: 's', name: 'N', enabled: true, welcome_message: 'hi', agent: 'plan', model: 'p/m', system_prompt: 'sp', branding: { logo: 'L', theme: 'x' } }
    const w = mountD(inst)
    await flushPromises()
    expect(w.get('[data-test="slug"]').attributes('disabled')).toBeDefined()
    expect((w.vm as any).slug).toBe('s')
    expect((w.vm as any).agent).toBe('plan')
    await (w.vm as any).save()
    expect(mockUpdate).toHaveBeenCalledWith('i1', {
      name: 'N', enabled: true, welcome_message: 'hi', agent: 'plan', model: 'p/m',
      system_prompt: 'sp', branding: { logo: 'L', theme: 'x' },
    })
    expect(mockCreate).not.toHaveBeenCalled()
  })

  it('canSave: false without name or with invalid slug (create)', async () => {
    const w = mountD(null)
    await flushPromises()
    expect((w.vm as any).canSave).toBe(false)
    ;(w.vm as any).name = 'X'; (w.vm as any).slug = 'BAD!'
    await flushPromises()
    expect((w.vm as any).canSave).toBe(false)
    ;(w.vm as any).slug = 'ok-1'
    await flushPromises()
    expect((w.vm as any).canSave).toBe(true)
  })

  it('empty agent/model → null in payload', async () => {
    const w = mountD(null)
    await flushPromises()
    ;(w.vm as any).slug = 'pw'; (w.vm as any).name = 'PW'
    await (w.vm as any).save()
    const payload = mockCreate.mock.calls[0][0]
    expect(payload.agent).toBeNull()
    expect(payload.model).toBeNull()
  })
})
