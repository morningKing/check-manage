import { describe, it, expect } from 'vitest'
import { splitArtifacts, artifactFilename, isMarkdownLang, sniffLang, isRenderableLang, isRunnableLang, isInlineRenderLang, isImageFile } from '../artifacts'

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

  it('keeps a large mermaid block inline (not lifted to an artifact)', () => {
    const diagram = ['graph TD', 'A-->B', 'B-->C', 'C-->D', 'D-->E', 'E-->F', 'F-->G'].join('\n')
    const src = `流程：\n\n\`\`\`mermaid\n${diagram}\n\`\`\`\n\n完。`
    const segs = splitArtifacts(src)
    expect(segs.every(s => s.type === 'text')).toBe(true)
    expect(segs.map(s => (s as any).text).join('')).toContain('graph TD')
  })

  it('keeps a large echarts block inline (not lifted to an artifact)', () => {
    const opt = ['{', '"xAxis": {"type":"category","data":["A","B"]},', '"yAxis": {"type":"value"},', '"series": [{"type":"bar","data":[1,2]}],', '"tooltip": {},', '"legend": {}', '}'].join('\n')
    const src = `图：\n\n\`\`\`echarts\n${opt}\n\`\`\`\n`
    const segs = splitArtifacts(src)
    expect(segs.every(s => s.type === 'text')).toBe(true)
  })

  it('still lifts a large non-diagram code block', () => {
    const code = Array.from({ length: 8 }, (_, i) => `line ${i}`).join('\n')
    const segs = splitArtifacts('```python\n' + code + '\n```')
    expect(segs.some(s => s.type === 'code')).toBe(true)
  })

  it('never lifts prose-language fences (text / plaintext / empty), regardless of size', () => {
    // A wall of plain text the model happened to wrap in ``` ought to stay
    // inline (rendered as a markdown code block), not become a "click to
    // preview" artifact card.
    const longText = Array.from({ length: 20 }, (_, i) => `line ${i} of plain prose`).join('\n')
    for (const lang of ['', 'text', 'plaintext']) {
      const src = '说明：\n\n```' + lang + '\n' + longText + '\n```\n'
      const segs = splitArtifacts(src)
      expect(segs.every(s => s.type === 'text')).toBe(true)
    }
  })

  it('keeps an inline mermaid block while still lifting a following large code block', () => {
    const py = Array.from({ length: 8 }, (_, i) => `line ${i}`).join('\n')
    const src = `图：\n\n\`\`\`mermaid\ngraph TD\nA-->B\n\`\`\`\n\n代码：\n\n\`\`\`python\n${py}\n\`\`\`\n`
    const segs = splitArtifacts(src)
    const codeSegs = segs.filter(s => s.type === 'code')
    expect(codeSegs).toHaveLength(1)
    expect((codeSegs[0] as any).lang).toBe('python')
    // the mermaid fence stays as raw markdown inside a text segment
    expect(segs.some(s => s.type === 'text' && (s as any).text.includes('graph TD'))).toBe(true)
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

describe('isRunnableLang', () => {
  it('is true for python only', () => {
    expect(isRunnableLang('python')).toBe(true)
    expect(isRunnableLang('py')).toBe(true)
    expect(isRunnableLang('check_disk.py')).toBe(true)
    expect(isRunnableLang('javascript')).toBe(false)
    expect(isRunnableLang('svg')).toBe(false)
  })
})

describe('isInlineRenderLang', () => {
  it('matches mermaid and echarts case-insensitively', () => {
    expect(isInlineRenderLang('mermaid')).toBe(true)
    expect(isInlineRenderLang('ECharts')).toBe(true)
    expect(isInlineRenderLang(' echarts ')).toBe(true)
  })
  it('does not match normal code langs', () => {
    expect(isInlineRenderLang('python')).toBe(false)
    expect(isInlineRenderLang('svg')).toBe(false)
    expect(isInlineRenderLang('')).toBe(false)
  })
})

describe('isImageFile', () => {
  it('detects image extensions case-insensitively', () => {
    expect(isImageFile('a.svg')).toBe(true)
    expect(isImageFile('a.PNG')).toBe(true)
    expect(isImageFile('photo.jpeg')).toBe(true)
    expect(isImageFile('x.webp')).toBe(true)
    expect(isImageFile('y.gif')).toBe(true)
  })
  it('returns false for non-images and names without an extension', () => {
    expect(isImageFile('a.txt')).toBe(false)
    expect(isImageFile('script.py')).toBe(false)
    expect(isImageFile('noext')).toBe(false)
    expect(isImageFile('svg')).toBe(false)
  })
})
