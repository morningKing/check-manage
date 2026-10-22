import { describe, it, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import * as Y from 'yjs'
import { applySeedIfEmpty, DOC_META_MAP, SKIP_INITIAL_ANALYSIS_KEY } from '../seed'

function make() {
  const ydoc = new Y.Doc()
  const editor = new Editor({ extensions: [StarterKit], content: '' })
  return { ydoc, editor }
}

describe('applySeedIfEmpty', () => {
  it('空正文应用 seed 并设置跳过首轮分析标记', () => {
    const { ydoc, editor } = make()
    expect(applySeedIfEmpty(editor, ydoc, '<h1>标题</h1><p>正文</p>')).toBe(true)
    expect(editor.getText()).toContain('标题')
    expect(ydoc.getMap(DOC_META_MAP).get(SKIP_INITIAL_ANALYSIS_KEY)).toBe(true)
    editor.destroy()
  })

  it('非空正文不覆盖，也不设置标记', () => {
    const { ydoc, editor } = make()
    editor.commands.setContent('<p>已有内容</p>')
    expect(applySeedIfEmpty(editor, ydoc, '<p>导入内容</p>')).toBe(false)
    expect(editor.getText()).toContain('已有内容')
    expect(ydoc.getMap(DOC_META_MAP).get(SKIP_INITIAL_ANALYSIS_KEY)).toBeUndefined()
    editor.destroy()
  })
})
