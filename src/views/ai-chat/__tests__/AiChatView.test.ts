import { describe, it, expect } from 'vitest'
import { batchStatusLabel, isBatchChildAutomatic } from '../batchChildState'

describe('AiChatView batch child controls', () => {
  it.each(['pending', 'running'] as const)('hides the composer while child is %s', (status) => {
    expect(isBatchChildAutomatic(status)).toBe(true)
  })

  it.each(['completed', 'failed', 'cancelled'] as const)('exposes the composer after child is %s', (status) => {
    expect(isBatchChildAutomatic(status)).toBe(false)
  })

  it('labels partial and cancelled terminal states', () => {
    expect(batchStatusLabel('partial')).toBe('部分失败')
    expect(batchStatusLabel('cancelled')).toBe('已取消')
  })
})
