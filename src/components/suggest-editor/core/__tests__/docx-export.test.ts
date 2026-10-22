import { describe, it, expect } from 'vitest'
import { exportDocx } from '../docx-export'

const json = {
  type: 'doc',
  content: [
    { type: 'heading', attrs: { level: 1 }, content: [{ type: 'text', text: '方案标题' }] },
    {
      type: 'paragraph',
      content: [
        { type: 'text', text: '加粗', marks: [{ type: 'bold' }] },
        { type: 'text', text: '与普通' },
      ],
    },
    {
      type: 'bulletList',
      content: [
        { type: 'listItem', content: [{ type: 'paragraph', content: [{ type: 'text', text: '事项一' }] }] },
      ],
    },
  ],
}

describe('exportDocx core', () => {
  it('生成可被 mammoth 回读的 docx', async () => {
    const blob = await exportDocx(json as never, {
      title: '测试',
      fetchImage: async () => ({ data: new ArrayBuffer(0), type: 'image/png' }),
    })
    const mammoth = await import('mammoth')
    // vitest 解析到 mammoth 的 Node 构建：它接受 buffer，不接受浏览器端的 arrayBuffer
    const { value } = await mammoth.extractRawText({
      buffer: Buffer.from(await blob.arrayBuffer()),
    })
    expect(value).toContain('方案标题')
    expect(value).toContain('加粗')
    expect(value).toContain('事项一')
  })

  it('非法 URL 链接降级为文本，不抛错', async () => {
    const bad = {
      type: 'doc',
      content: [{
        type: 'paragraph',
        content: [{
          type: 'text',
          text: '坏链接',
          marks: [{ type: 'link', attrs: { href: 'javascript:alert(1)' } }],
        }],
      }],
    }
    const blob = await exportDocx(bad as never, {
      title: 'x',
      fetchImage: async () => ({ data: new ArrayBuffer(0), type: 'image/png' }),
    })
    expect(blob.size).toBeGreaterThan(0)
  })

  it('表格/图片/图注进入导出文件', async () => {
    const png = Uint8Array.from(atob(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lwJ2XwAAAABJRU5ErkJggg==',
    ), (c) => c.charCodeAt(0))
    const withTable = {
      type: 'doc',
      content: [
        {
          type: 'table',
          content: [
            {
              type: 'tableRow',
              content: [
                { type: 'tableHeader', content: [{ type: 'paragraph', content: [{ type: 'text', text: '设备' }] }] },
                { type: 'tableHeader', content: [{ type: 'paragraph', content: [{ type: 'text', text: '数量' }] }] },
              ],
            },
            {
              type: 'tableRow',
              content: [
                { type: 'tableCell', content: [{ type: 'paragraph', content: [{ type: 'text', text: '9800M12' }] }] },
                { type: 'tableCell', content: [{ type: 'paragraph', content: [{ type: 'text', text: '3' }] }] },
              ],
            },
          ],
        },
        {
          type: 'figure',
          content: [
            { type: 'image', attrs: { src: '/suggest-editor/attachments/att_1', alt: '拓扑' } },
            { type: 'figureCaption', content: [{ type: 'text', text: '图 1 拓扑' }] },
          ],
        },
      ],
    }
    const blob = await exportDocx(withTable as never, {
      title: 'x',
      fetchImage: async () => ({ data: png.buffer, type: 'image/png' }),
    })
    const mammoth = await import('mammoth')
    const html = (await mammoth.convertToHtml({
      buffer: Buffer.from(await blob.arrayBuffer()),
    })).value
    expect(html).toContain('<table>')
    expect(html).toContain('9800M12')
    expect(html).toContain('图 1 拓扑')
    expect(html).toContain('<img')
  })
})
