import { describe, it, expect } from 'vitest'
import { avatarInitial, avatarColor, AVATAR_COLORS } from '../avatar'

describe('avatarInitial', () => {
  it('returns first char of name', () => {
    expect(avatarInitial('演示客服')).toBe('演')
    expect(avatarInitial('Acme')).toBe('A')
  })
  it('falls back to 客 when empty/undefined', () => {
    expect(avatarInitial('')).toBe('客')
    expect(avatarInitial(undefined)).toBe('客')
    expect(avatarInitial('   ')).toBe('客')
  })
})

describe('avatarColor', () => {
  it('returns a palette color', () => {
    expect(AVATAR_COLORS).toContain(avatarColor('演示客服'))
  })
  it('is stable for the same name', () => {
    expect(avatarColor('演示客服')).toBe(avatarColor('演示客服'))
  })
  it('falls back deterministically for empty name', () => {
    expect(AVATAR_COLORS).toContain(avatarColor(''))
    expect(avatarColor('')).toBe(avatarColor(undefined))
  })
})
