import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import CreateBatchDialog from '../CreateBatchDialog.vue'
import * as batchApi from '@/api/aiChatBatches'
import * as tplApi from '@/api/aiChatPromptTemplates'

vi.mock('@/api/aiChatBatches')
vi.mock('@/api/aiChatPromptTemplates')

beforeAll(() => {
  globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(tplApi.listTemplates).mockResolvedValue([])
})

const stubs = {
  'el-dialog': { template: '<div><slot /><slot name="footer" /></div>' },
  'el-input':  {
    template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-select': {
    template: '<select><slot /></select>',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-option':   { template: '<option />' },
  'el-checkbox': {
    template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-button': { template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>', props: ['disabled'], emits: ['click'] },
  'el-upload': true,
}

describe('CreateBatchDialog', () => {
  it('disables 创建 until name + prompt + ≥1 staged file', async () => {
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    const create = w.find('button[data-test="create-btn"]')
    expect((create.element as HTMLButtonElement).disabled).toBe(true)
    // simulate name + prompt
    await w.find('input[data-test="name"]').setValue('B1')
    await w.find('input[data-test="prompt"]').setValue('do')
    expect((create.element as HTMLButtonElement).disabled).toBe(true)
    // simulate one staged file present
    ;(w.vm as any).stagedFiles = [{ name: 'a.txt', path: 'batch-staging/x/y/a.txt' }]
    await flushPromises()
    expect((w.find('button[data-test="create-btn"]').element as HTMLButtonElement).disabled).toBe(false)
  })

  it('calls createBatch with the staged file list on submit', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B1', prompt: 'do',
               template_id: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B1')
    await w.find('input[data-test="prompt"]').setValue('do')
    ;(w.vm as any).stagedFiles = [{ name: 'a.txt', path: 'p/a.txt' }]
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(batchApi.createBatch).toHaveBeenCalledWith(expect.objectContaining({
      name: 'B1', prompt: 'do',
      files: [{ name: 'a.txt', path: 'p/a.txt' }],
    }))
  })

  it('saves a new template when 保存为模板 is checked', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B', prompt: 'p',
               template_id: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    vi.mocked(tplApi.createTemplate).mockResolvedValue({} as any)
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B')
    await w.find('input[data-test="prompt"]').setValue('hello')
    ;(w.vm as any).stagedFiles = [{ name: 'a', path: 'p' }]
    ;(w.vm as any).saveAsTemplate = true
    ;(w.vm as any).templateName = 'My T'
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(tplApi.createTemplate).toHaveBeenCalledWith('My T', 'hello')
  })
})
