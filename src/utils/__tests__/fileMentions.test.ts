import { describe, it, expect } from 'vitest'
import { activeAtToken, filterFileItems, splitMentionSegments, type AiFileLike } from '../fileMentions'

describe('activeAtToken', () => {
  it('captures a path token with separators and dots', () => {
    expect(activeAtToken('帮我看 @src/main.ts', 16)).toEqual({ query: 'src/main.ts', start: 4, end: 16 })
  })
  it('captures uploads/ and outputs/ prefixes', () => {
    expect(activeAtToken('@uploads/da', 11)?.query).toBe('uploads/da')
    expect(activeAtToken('@outputs/report', 15)?.query).toBe('outputs/report')
  })
  it('plain word still matches (agent token)', () => {
    expect(activeAtToken('hi @gen', 7)?.query).toBe('gen')
  })
  it('empty query right after @', () => {
    expect(activeAtToken('hi @', 4)).toEqual({ query: '', start: 3, end: 4 })
  })
  it('returns null once the token is followed by a space', () => {
    expect(activeAtToken('@gen now', 8)).toBeNull()
  })
  it('does not treat emails as mentions (@ must follow start/whitespace)', () => {
    expect(activeAtToken('a@b.com', 7)).toBeNull()
  })
  it('supports CJK paths', () => {
    expect(activeAtToken('分析 @uploads/数据.csv', 18)?.query).toBe('uploads/数据.csv')
  })
})

const FILES: AiFileLike[] = [
  { name: 'data.csv', path: 'uploads/data.csv', dir: 'uploads', size: 1024 },
  { name: 'report.json', path: 'outputs/report.json', dir: 'outputs', size: 2048 },
  { name: 'helper.ts', path: 'src/utils/helper.ts', dir: 'workspace', size: 512 },
  { name: 'data-2.csv', path: 'uploads/data-2.csv', dir: 'uploads', size: 800 },
]

describe('filterFileItems', () => {
  it('matches by substring on path (case-insensitive)', () => {
    const r = filterFileItems(FILES, 'DATA')
    expect(r.map((f) => f.path).sort()).toEqual(['uploads/data-2.csv', 'uploads/data.csv'])
  })
  it('ranks basename matches ahead of path matches', () => {
    const r = filterFileItems(FILES, 'helper')
    expect(r[0].path).toBe('src/utils/helper.ts')
  })
  it('matches by directory segment', () => {
    expect(filterFileItems(FILES, 'outputs').map((f) => f.path)).toEqual(['outputs/report.json'])
  })
  it('empty query returns files (bounded by limit)', () => {
    expect(filterFileItems(FILES, '').length).toBe(FILES.length)
    const many: AiFileLike[] = Array.from({ length: 30 }, (_, i) => ({
      name: `f${i}.txt`, path: `outputs/f${i}.txt`, dir: 'outputs', size: 1,
    }))
    expect(filterFileItems(many, '').length).toBe(20)
  })
})

describe('splitMentionSegments', () => {
  const known = new Set(FILES.map((f) => f.path))

  it('splits a known file mention into a chip segment', () => {
    const segs = splitMentionSegments('分析 @uploads/data.csv 这个', known)
    expect(segs).toEqual([
      { type: 'text', text: '分析 ' },
      { type: 'file', path: 'uploads/data.csv' },
      { type: 'text', text: ' 这个' },
    ])
  })
  it('renders a mention at start of string', () => {
    const segs = splitMentionSegments('@outputs/report.json 好了', known)
    expect(segs[0]).toEqual({ type: 'file', path: 'outputs/report.json' })
  })
  it('leaves unknown paths and agent names as plain text', () => {
    const segs = splitMentionSegments('问 @build 看 @uploads/missing.csv', known)
    expect(segs.every((s) => s.type === 'text')).toBe(true)
    expect(segs[0]).toEqual({ type: 'text', text: '问 @build 看 @uploads/missing.csv' })
  })
  it('does not split emails', () => {
    const segs = splitMentionSegments('联系 a@b.com 谢谢', known)
    expect(segs).toEqual([{ type: 'text', text: '联系 a@b.com 谢谢' }])
  })
  it('emits multiple file chips in order', () => {
    const segs = splitMentionSegments('@uploads/data.csv 和 @outputs/report.json', known)
    expect(segs.filter((s) => s.type === 'file').map((s) => (s as { path: string }).path)).toEqual([
      'uploads/data.csv', 'outputs/report.json',
    ])
  })
})
