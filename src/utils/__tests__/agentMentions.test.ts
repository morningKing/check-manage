import { describe, it, expect } from 'vitest'
import { activeMentionToken, parseAgentMentions } from '../agentMentions'

describe('activeMentionToken', () => {
  it('detects @ at start of input', () => {
    expect(activeMentionToken('@gen', 4)).toEqual({ query: 'gen', start: 0, end: 4 })
  })
  it('detects @ after whitespace mid-text', () => {
    expect(activeMentionToken('hi @ex', 6)).toEqual({ query: 'ex', start: 3, end: 6 })
  })
  it('returns empty query right after typing @', () => {
    expect(activeMentionToken('hi @', 4)).toEqual({ query: '', start: 3, end: 4 })
  })
  it('no token when @ is preceded by non-whitespace (email-like)', () => {
    expect(activeMentionToken('a@b', 3)).toBeNull()
  })
  it('no token when a space already follows the mention', () => {
    expect(activeMentionToken('@gen now', 8)).toBeNull()
  })
})

describe('parseAgentMentions', () => {
  const known = new Set(['general', 'explore'])
  it('parses one mention with offsets', () => {
    expect(parseAgentMentions('ask @general ok', known)).toEqual([
      { name: 'general', value: '@general', start: 4, end: 12 },
    ])
  })
  it('parses multiple and skips unknown names', () => {
    const out = parseAgentMentions('@general and @nope and @explore', known)
    expect(out.map((m) => m.name)).toEqual(['general', 'explore'])
  })
  it('requires whitespace (or start) before @', () => {
    expect(parseAgentMentions('a@general', known)).toEqual([])
  })
})
