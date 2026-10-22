import { describe, it, expect } from 'vitest'
import { Document, Packer, Paragraph, TextRun, ImageRun } from 'docx'
import {
  sanitizeImportedHtml, replaceImagePlaceholders, parseDocx, DocxImportError, assertImportLimits,
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

  it('空 src 的 img（被跳过的不支持图片）连同独占段落移除', () => {
    const { html } = sanitizeImportedHtml('<p>前</p><p><img src=""></p><p>后</p>')
    expect(html).toContain('<p>前</p>')
    expect(html).toContain('<p>后</p>')
    expect(html).not.toContain('<img')
  })
})

describe('assertImportLimits', () => {
  it('文本/块数超限抛 DocxImportError', () => {
    expect(() => assertImportLimits(2 * 1024 * 1024 + 1, 10)).toThrow(DocxImportError)
    expect(() => assertImportLimits(100, 2001)).toThrow(DocxImportError)
  })
})

describe('parseDocx 不支持的图片', () => {
  const PNG_B64 =
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lwJ2XwAAAABJRU5ErkJggg=='
  const PNG = Buffer.from(PNG_B64, 'base64')
  // 假 BMP：前端按 contentType（image/bmp）判定跳过，magic bytes 只需符合 BMP 规范头
  const FAKE_BMP = Buffer.concat([Buffer.from('BM'), Buffer.alloc(64, 0)])

  async function makeDocx(images: Array<{ type: 'png' | 'bmp'; data: Buffer }>): Promise<File> {
    const doc = new Document({
      sections: [{
        children: [
          new Paragraph({ children: [new TextRun('正文第一段')] }),
          ...images.map((image) => new Paragraph({
            children: [new ImageRun({
              type: image.type, data: image.data, transformation: { width: 20, height: 20 },
            })],
          })),
        ],
      }],
    })
    const buffer = await Packer.toBuffer(doc)
    // 显式拷进 ArrayBuffer 视角（Node Buffer 的 ArrayBufferLike 不满足 BlobPart 约束）
    const bytes = new Uint8Array(buffer.byteLength)
    bytes.set(buffer)
    return new File([bytes], 'fixture.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
  }

  it('png 照常导入；不支持的 bmp 只跳过该图并计入提示', async () => {
    const parsed = await parseDocx(await makeDocx([
      { type: 'png', data: PNG },
      { type: 'bmp', data: FAKE_BMP },
    ]))
    expect(parsed.images.map((image) => image.id)).toEqual(['img-1'])
    expect(parsed.images[0].blob.type).toBe('image/png')
    expect(parsed.warnings.join('')).toContain('已跳过 1 张不支持的图片')
    expect((parsed.html.match(/<figure/g) ?? []).length).toBe(1)
    expect(parsed.html).toContain('正文第一段')
  })

  it('全部图片都不支持时正文照常导入（不抛错）', async () => {
    const parsed = await parseDocx(await makeDocx([
      { type: 'bmp', data: FAKE_BMP },
      { type: 'bmp', data: FAKE_BMP },
    ]))
    expect(parsed.images).toHaveLength(0)
    expect(parsed.warnings.join('')).toContain('已跳过 2 张不支持的图片')
    expect(parsed.html).toContain('正文第一段')
    expect(parsed.html).not.toContain('<figure')
    expect(parsed.html).not.toContain('se-img://')
  })
})
