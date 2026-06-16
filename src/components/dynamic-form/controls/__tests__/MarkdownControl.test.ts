import { describe, it, expect } from 'vitest'
import { controlComponentMap, getControlComponent, getControlDefaultValue } from '../index'
import { CONTROL_TYPE_OPTIONS } from '@/types/field'

describe('markdown field control', () => {
  it('is registered in the control component map (distinct from fallback text)', () => {
    expect(controlComponentMap.markdown).toBeTruthy()
    expect(controlComponentMap.markdown).not.toBe(controlComponentMap.text)
    expect(getControlComponent('markdown')).toBe(controlComponentMap.markdown)
  })

  it('defaults to an empty string (Markdown source text)', () => {
    expect(getControlDefaultValue('markdown')).toBe('')
  })

  it('appears in the control-type option list', () => {
    const opt = CONTROL_TYPE_OPTIONS.find((o) => o.value === 'markdown')
    expect(opt).toBeTruthy()
    expect(opt?.label).toBe('Markdown')
  })
})
