import { describe, it, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { TABLE_EXTENSIONS, insertInitialTable } from '../table'

function editor() {
  return new Editor({ extensions: [StarterKit, ...TABLE_EXTENSIONS], content: '<p>x</p>' })
}

type TableNode = { type?: string; content?: Array<{ type?: string; content?: unknown[] }> }

function tableOf(e: Editor): TableNode | undefined {
  const content = e.getJSON().content ?? []
  return content.find((n) => n.type === 'table') as TableNode | undefined
}

describe('table support', () => {
  it('插入 3x3 带表头的表格', () => {
    const e = editor()
    insertInitialTable(e)
    const table = tableOf(e)
    expect(table).toBeTruthy()
    expect(table?.content).toHaveLength(3)
    expect(table?.content?.[0]?.content?.[0]).toMatchObject({ type: 'tableHeader' })
    e.destroy()
  })

  it('增删行列命令生效', () => {
    const e = editor()
    insertInitialTable(e)
    e.chain().focus().addRowAfter().run()
    e.chain().focus().addColumnAfter().run()
    expect(tableOf(e)?.content).toHaveLength(4)
    e.chain().focus().deleteRow().run()
    e.chain().focus().deleteColumn().run()
    expect(tableOf(e)?.content).toHaveLength(3)
    e.destroy()
  })
})
