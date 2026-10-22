import { describe, it, expect, beforeEach } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { FIGURE_EXTENSIONS, insertFigure } from '../figure'
import { attachmentSrc, attachmentFetchPath } from '../attachment-url'

beforeEach(() => {
  localStorage.setItem('check-manage:token', JSON.stringify('tok-1'))
})

function editor() {
  return new Editor({ extensions: [StarterKit, ...FIGURE_EXTENSIONS], content: '<p>x</p>' })
}

describe('figure', () => {
  it('插入 figure 后 JSON 含 image + figureCaption', () => {
    const e = editor()
    insertFigure(e, { src: '/suggest-editor/attachments/att_1', alt: '拓扑' })
    const fig = e.getJSON().content?.find((n) => n.type === 'figure') as
      | { content?: Array<{ type?: string; attrs?: Record<string, unknown> }> }
      | undefined
    expect(fig?.content?.[0]?.attrs?.src).toBe('/suggest-editor/attachments/att_1')
    expect(fig?.content?.[1]?.type).toBe('figureCaption')
    e.destroy()
  })

  it('attachmentSrc 拼 /api 与 access_token，非附件 URL 原样返回', () => {
    expect(attachmentSrc('/suggest-editor/attachments/att_1'))
      .toBe('/api/suggest-editor/attachments/att_1?access_token=tok-1')
    expect(attachmentSrc('https://example.com/a.png')).toBe('https://example.com/a.png')
    expect(attachmentFetchPath('/suggest-editor/attachments/att_1'))
      .toBe('/api/suggest-editor/attachments/att_1')
  })

  it('图注可编辑并进入 HTML', () => {
    const e = editor()
    insertFigure(e, { src: '/suggest-editor/attachments/att_1', alt: '' })
    let captionPos = -1
    e.state.doc.descendants((node, pos) => {
      if (node.type.name === 'figureCaption') captionPos = pos + 1
    })
    expect(captionPos).toBeGreaterThan(0)
    e.chain().focus().setTextSelection(captionPos).insertContent('图 1 拓扑').run()
    expect(e.getHTML()).toContain('<figcaption>图 1 拓扑</figcaption>')
    e.destroy()
  })
})
