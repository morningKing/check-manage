import { describe, it, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import {
  FORMATTING_EXTENSIONS, clearFormatting, FONT_SIZES,
} from '../formatting'

function makeEditor(content: string, withFormatting = true) {
  return new Editor({
    extensions: [StarterKit, ...(withFormatting ? FORMATTING_EXTENSIONS : [])],
    content,
  })
}

describe('formatting extensions', () => {
  it('字号/颜色/高亮/对齐可设置并持久化到 HTML', () => {
    const editor = makeEditor('<p>设备类型</p>')
    editor.chain().selectAll()
      .setFontSize('16pt')
      .setColor('#c0392b')
      .setHighlight({ color: '#fff3a3' })
      .setTextAlign('center')
      .run()
    const html = editor.getHTML()
    expect(html).toContain('font-size: 16pt')
    expect(html).toContain('text-align: center')
    // DOM 序列化会把 hex 转成 rgb；断言编辑器属性（真实状态）而非 CSS 字面量
    const textStyle = editor.getAttributes('textStyle')
    expect(textStyle.color).toBe('#c0392b')
    expect(textStyle.fontSize).toBe('16pt')
    expect(editor.getAttributes('highlight').color).toBe('#fff3a3')
    expect(editor.getAttributes('paragraph').textAlign).toBe('center')
    editor.destroy()
  })

  it('clearFormatting 清除 marks 与对齐', () => {
    const editor = makeEditor('<p>设备类型</p>')
    editor.chain().selectAll()
      .setFontSize('16pt').setColor('#c0392b')
      .setHighlight({ color: '#fff3a3' }).setTextAlign('center')
      .run()
    clearFormatting(editor)
    const html = editor.getHTML()
    expect(html).not.toContain('font-size')
    expect(html).not.toContain('color:')
    expect(html).not.toContain('background-color')
    expect(html).not.toContain('text-align')
    editor.destroy()
  })

  it('字号清单包含 12/14/16/18/20/24', () => {
    expect(FONT_SIZES).toEqual(['12pt', '14pt', '16pt', '18pt', '20pt', '24pt'])
  })
})
