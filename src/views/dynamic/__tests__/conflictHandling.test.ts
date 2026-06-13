import { describe, it, expect } from 'vitest'
import { isVersionConflict, conflictMessage } from '@/views/dynamic/conflict'

describe('版本冲突识别', () => {
  it('识别 409 + VERSION_CONFLICT', () => {
    expect(isVersionConflict({ response: { status: 409, data: { code: 'VERSION_CONFLICT' } } })).toBe(true)
  })
  it('普通错误不算冲突', () => {
    expect(isVersionConflict({ response: { status: 400, data: {} } })).toBe(false)
    expect(isVersionConflict(new Error('x'))).toBe(false)
  })
  it('冲突文案固定', () => {
    expect(conflictMessage()).toContain('其他用户修改')
  })
})
