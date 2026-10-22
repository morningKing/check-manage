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
})
