import { describe, it, expect } from 'vitest'
import {
  sanitizeImportedHtml, replaceImagePlaceholders, DocxImportError, assertImportLimits,
} from '../docx-import'

describe('sanitizeImportedHtml', () => {
  it('移除 script/style/on*，保留允许标签', () => {
    const { html, warnings } = sanitizeImportedHtml(
      '<h1 onclick="x()">标题</h1><script>alert(1)</script><p style="color:red">正文</p>')
    expect(html).toContain('<h1>标题</h1>')
    expect(html).toContain('<p>正文</p>')
    expect(html).not.toContain('script')
    expect(html).not.toContain('onclick')
    expect(warnings.length).toBeGreaterThan(0)
  })

  it('img 转 figure + figcaption 占位并保留 src', () => {
    const { html } = sanitizeImportedHtml('<p><img src="se-img://img-1" alt="拓扑"></p>')
    expect(html).toContain('<figure>')
    expect(html).toContain('src="se-img://img-1"')
    expect(html).toContain('<figcaption></figcaption>')
  })

  it('嵌套表格降级并告警', () => {
    const { html, warnings } = sanitizeImportedHtml(
      '<table><tr><td><table><tr><td>x</td></tr></table></td></tr></table>')
    expect(html.match(/<table/g)?.length).toBe(1)
    expect(warnings.join('')).toContain('嵌套表格')
  })

  it('替换图片占位符', () => {
    const html = '<figure><img src="se-img://img-1"><figcaption></figcaption></figure>'
    expect(replaceImagePlaceholders(html, new Map([['img-1', '/suggest-editor/attachments/att_1']])))
      .toContain('src="/suggest-editor/attachments/att_1"')
  })
})

describe('assertImportLimits', () => {
  it('文本/块数超限抛 DocxImportError', () => {
    expect(() => assertImportLimits(2 * 1024 * 1024 + 1, 10)).toThrow(DocxImportError)
    expect(() => assertImportLimits(100, 2001)).toThrow(DocxImportError)
  })
})
