import { describe, it, expect } from 'vitest'
import { previewKind, isPreviewable } from '../filePreview'

describe('previewKind', () => {
  it('识别 office 类型（docx/excel/pptx/pdf）', () => {
    expect(previewKind('report.docx')).toBe('docx')
    expect(previewKind('data.xlsx')).toBe('excel')
    expect(previewKind('data.xls')).toBe('excel')
    expect(previewKind('deck.pptx')).toBe('pptx')
    expect(previewKind('doc.pdf')).toBe('pdf')
  })

  it('识别文本/Markdown/图片类型', () => {
    expect(previewKind('notes.md')).toBe('markdown')
    expect(previewKind('log.txt')).toBe('text')
    expect(previewKind('data.csv')).toBe('text')
    expect(previewKind('pic.png')).toBe('image')
  })

  it('大小写不敏感', () => {
    expect(previewKind('DATA.XLSX')).toBe('excel')
  })

  it('未知/无扩展名归为 unsupported', () => {
    expect(previewKind('archive.zip')).toBe('unsupported')
    expect(previewKind('README')).toBe('unsupported')
    expect(previewKind(undefined)).toBe('unsupported')
    expect(previewKind(null)).toBe('unsupported')
  })
})

describe('isPreviewable', () => {
  it('excel 等可预览类型返回 true', () => {
    expect(isPreviewable('data.xlsx')).toBe(true)
  })

  it('unsupported 类型返回 false', () => {
    expect(isPreviewable('archive.zip')).toBe(false)
  })
})
