import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuComposer from '../KefuComposer.vue'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })

const stubs = {
  'el-input': {
    template: `<textarea class="ci" :value="modelValue"
      @input="$emit('update:modelValue',$event.target.value)"
      @keydown="$emit('keydown',$event)" @focus="$emit('focus')" @blur="$emit('blur')" />`,
    props: ['modelValue'],
  },
  'el-icon': { template: '<i><slot/></i>' },
}
const mountC = (props: any = {}) =>
  mount(KefuComposer, { props: { draft: '', pending: [], sending: false, ...props }, global: { stubs } })

describe('KefuComposer', () => {
  it('emits update:draft on input', async () => {
    const w = mountC()
    await w.find('textarea.ci').setValue('你好')
    expect(w.emitted('update:draft')![0]).toEqual(['你好'])
  })

  it('Enter (no shift, not composing) emits send', async () => {
    const w = mountC({ draft: '你好' })
    await w.find('textarea.ci').trigger('keydown.enter')
    expect(w.emitted('send')).toBeTruthy()
  })

  it('Shift+Enter does NOT emit send', async () => {
    const w = mountC({ draft: '你好' })
    await w.find('textarea.ci').trigger('keydown.enter', { shiftKey: true })
    expect(w.emitted('send')).toBeFalsy()
  })

  it('IME composing Enter does NOT emit send', async () => {
    const w = mountC({ draft: '你好' })
    await w.find('textarea.ci').trigger('keydown.enter', { isComposing: true })
    expect(w.emitted('send')).toBeFalsy()
  })

  it('Enter with empty draft does NOT emit send', async () => {
    const w = mountC({ draft: '   ' })
    await w.find('textarea.ci').trigger('keydown.enter')
    expect(w.emitted('send')).toBeFalsy()
  })

  it('file pick emits pickFiles with File[]', async () => {
    const w = mountC()
    const input = w.find('input[type=file]').element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [new File(['x'], 'a.txt')] })
    await w.find('input[type=file]').trigger('change')
    expect(w.emitted('pickFiles')![0][0]).toHaveLength(1)
  })

  it('renders pending chips and ✕ emits removePending', async () => {
    const w = mountC({ pending: [{ name: 'a.txt', path: 'uploads/a.txt' }] })
    const chip = w.find('.composer__chip')
    expect(chip.text()).toContain('a.txt')
    await chip.find('b').trigger('click')
    expect(w.emitted('removePending')![0]).toEqual([0])
  })

  it('send button disabled when empty or sending', () => {
    expect(mountC({ draft: '' }).find('.composer__send').attributes('disabled')).toBeDefined()
    expect(mountC({ draft: 'x', sending: true }).find('.composer__send').attributes('disabled')).toBeDefined()
    expect(mountC({ draft: 'x' }).find('.composer__send').attributes('disabled')).toBeUndefined()
  })
})
