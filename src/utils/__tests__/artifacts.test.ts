import { describe, it, expect } from 'vitest'
import { splitArtifacts, artifactFilename, isMarkdownLang } from '../artifacts'

describe('splitArtifacts', () => {
  it('lifts a large code block into a code segment', () => {
    const code = Array.from({ length: 8 }, (_, i) => `line ${i}`).join('\n')
    const src = `先看脚本：\n\n\`\`\`python\n${code}\n\`\`\`\n\n完成。`
    const segs = splitArtifacts(src)
    expect(segs.map(s => s.type)).toEqual(['text', 'code', 'text'])
    const codeSeg = segs.find(s => s.type === 'code') as any
    expect(codeSeg.lang).toBe('python')
    expect(codeSeg.code).toContain('line 0')
  })

  it('leaves small inline snippets in the prose', () => {
    const src = '用 `ls` 看下：\n\n```bash\nls\n```\n'
    const segs = splitArtifacts(src)
    // small block stays inside a single text segment (rendered inline by markdown)
    expect(segs.every(s => s.type === 'text')).toBe(true)
  })

  it('returns the whole text when there are no code blocks', () => {
    const segs = splitArtifacts('just prose')
    expect(segs).toEqual([{ type: 'text', text: 'just prose' }])
  })
})

describe('artifactFilename', () => {
  it('maps language to an extension', () => {
    expect(artifactFilename('python', 0)).toBe('artifact-1.py')
    expect(artifactFilename('typescript', 1)).toBe('artifact-2.ts')
    expect(artifactFilename('', 0)).toBe('artifact-1.txt')
  })
  it('uses the info string directly when it looks like a filename', () => {
    expect(artifactFilename('check_disk.py', 0)).toBe('check_disk.py')
  })
})

describe('isMarkdownLang', () => {
  it('detects markdown', () => {
    expect(isMarkdownLang('md')).toBe(true)
    expect(isMarkdownLang('markdown')).toBe(true)
    expect(isMarkdownLang('python')).toBe(false)
  })
})
