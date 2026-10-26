import { afterEach, describe, expect, it } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { findMatches, replaceAll, replaceOne } from '../find-replace'

const PARAGRAPH_TEXT = '设备检查记录：设备检查完成。'

function editorWithParagraph(text: string) {
  return new Editor({
    extensions: [StarterKit],
    content: {
      type: 'doc',
      content: [{ type: 'paragraph', content: [{ type: 'text', text }] }],
    },
  })
}

const editors: Editor[] = []
function tracked(editor: Editor): Editor {
  editors.push(editor)
  return editor
}

afterEach(() => {
  for (const editor of editors.splice(0)) editor.destroy()
})

describe('findMatches', () => {
  it('同段多处命中（位置按文档坐标）', () => {
    const editor = tracked(editorWithParagraph(PARAGRAPH_TEXT))
    // 文本从位置 1 开始：「设备检查」第一次在 offset 0，第二次在 offset 7
    expect(findMatches(editor.state.doc, '设备检查')).toEqual([
      { from: 1, to: 5 },
      { from: 8, to: 12 },
    ])
  })

  it('默认大小写不敏感，可显式开启敏感', () => {
    const editor = tracked(editorWithParagraph('UPS ups Ups'))
    expect(findMatches(editor.state.doc, 'ups')).toHaveLength(3)
    expect(findMatches(editor.state.doc, 'ups', true)).toHaveLength(1)
  })

  it('跨块不命中（各文本块独立匹配）', () => {
    const editor = tracked(
      new Editor({
        extensions: [StarterKit],
        content: {
          type: 'doc',
          content: [
            { type: 'paragraph', content: [{ type: 'text', text: '设备' }] },
            { type: 'paragraph', content: [{ type: 'text', text: '检查' }] },
          ],
        },
      }),
    )
    expect(findMatches(editor.state.doc, '设备检查')).toEqual([])
    expect(findMatches(editor.state.doc, '设备')).toHaveLength(1)
  })

  it('空查询返回空', () => {
    const editor = tracked(editorWithParagraph(PARAGRAPH_TEXT))
    expect(findMatches(editor.state.doc, '')).toEqual([])
  })
})

describe('replaceOne / replaceAll', () => {
  it('替换单处命中，一次撤销可还原', () => {
    const editor = tracked(editorWithParagraph(PARAGRAPH_TEXT))
    const [match] = findMatches(editor.state.doc, '设备检查')
    expect(replaceOne(editor, match, '巡检')).toBe(true)
    expect(editor.state.doc.textContent).toContain('巡检记录：设备检查完成。')
    editor.commands.undo()
    expect(editor.state.doc.textContent).toBe(PARAGRAPH_TEXT)
  })

  it('全部替换在一个事务里：一次撤销整体还原', () => {
    const editor = tracked(editorWithParagraph(PARAGRAPH_TEXT))
    const matches = findMatches(editor.state.doc, '设备检查')
    expect(replaceAll(editor, matches, '巡检')).toBe(2)
    expect(editor.state.doc.textContent).toBe('巡检记录：巡检完成。')
    editor.commands.undo()
    expect(editor.state.doc.textContent).toBe(PARAGRAPH_TEXT)
  })

  it('替换串为空即删除', () => {
    const editor = tracked(editorWithParagraph('方案A方案A'))
    const matches = findMatches(editor.state.doc, '方案A')
    expect(replaceAll(editor, matches, '')).toBe(2)
    expect(editor.state.doc.textContent).toBe('')
  })

  it('范围失效（跨块/越界）安全跳过，不破坏文档', () => {
    const editor = tracked(editorWithParagraph(PARAGRAPH_TEXT))
    // from=0 落在段落节点边界上，不在同一文本块内 → 拒绝替换
    expect(replaceOne(editor, { from: 0, to: 3 }, 'X')).toBe(false)
    expect(editor.state.doc.textContent).toBe(PARAGRAPH_TEXT)
    // 越界范围同理跳过
    expect(replaceAll(editor, [{ from: 0, to: 4 }, { from: 9999, to: 10003 }], 'X')).toBe(0)
    expect(editor.state.doc.textContent).toBe(PARAGRAPH_TEXT)
  })
})
