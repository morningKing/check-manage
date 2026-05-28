import { describe, it, expect } from 'vitest'
import { splitArtifacts, artifactFilename, isMarkdownLang, sniffLang, isRenderableLang } from '../artifacts'

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
    expect(artifactFilename('svg', 0)).toBe('artifact-1.svg')
    expect(artifactFilename('html', 0)).toBe('artifact-1.html')
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

describe('sniffLang', () => {
  it('detects html and svg from content', () => {
    expect(sniffLang('', '<!DOCTYPE html><html><body>hi</body></html>')).toBe('html')
    expect(sniffLang('text', '<svg viewBox="0 0 10 10"><rect/></svg>')).toBe('svg')
  })
  it('reclassifies Python mislabeled as bash', () => {
    expect(sniffLang('bash', 'import os\ndef main():\n    print(os.getcwd())')).toBe('python')
  })
  it('infers python/js/json/sql when label is generic', () => {
    expect(sniffLang('', 'def f():\n    return 1')).toBe('python')
    expect(sniffLang('', 'const a = () => 1\nconsole.log(a())')).toBe('javascript')
    expect(sniffLang('', 'SELECT * FROM users;')).toBe('sql')
  })
  it('keeps a correct explicit label', () => {
    expect(sniffLang('python', 'x = 1')).toBe('python')
    expect(sniffLang('go', 'package main')).toBe('go')
  })
})

describe('isRenderableLang', () => {
  it('is true for html/svg only', () => {
    expect(isRenderableLang('html')).toBe(true)
    expect(isRenderableLang('svg')).toBe(true)
    expect(isRenderableLang('python')).toBe(false)
  })
})
