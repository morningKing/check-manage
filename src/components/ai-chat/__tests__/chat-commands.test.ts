import { describe, it, expect } from 'vitest'
import { findFrontendCommand, parseCommandLine, FRONTEND_COMMANDS } from '@/components/ai-chat/chat-commands'

describe('parseCommandLine', () => {
  it('returns null for non-slash text', () => {
    expect(parseCommandLine('hello')).toBeNull()
    expect(parseCommandLine('  hi ')).toBeNull()
  })
  it('parses name and args', () => {
    expect(parseCommandLine('/mcps')).toEqual({ name: 'mcps', args: '' })
    expect(parseCommandLine('/init do the thing')).toEqual({ name: 'init', args: 'do the thing' })
  })
})

describe('findFrontendCommand', () => {
  it('finds /mcps and the /mcp alias, case-insensitively', () => {
    expect(findFrontendCommand('mcps')?.name).toBe('mcps')
    expect(findFrontendCommand('MCP')?.name).toBe('mcps')
    expect(findFrontendCommand('init')).toBeUndefined()
  })
  it('every registry command has name/description/run', () => {
    for (const c of FRONTEND_COMMANDS) {
      expect(typeof c.name).toBe('string')
      expect(typeof c.description).toBe('string')
      expect(typeof c.run).toBe('function')
    }
  })
})
