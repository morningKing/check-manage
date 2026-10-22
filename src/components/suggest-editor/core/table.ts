import { TableKit } from '@tiptap/extension-table'
import type { Editor } from '@tiptap/core'

export const TABLE_EXTENSIONS = [
  TableKit.configure({
    table: { resizable: true, lastColumnResizable: true },
  }),
]

export function insertInitialTable(editor: Editor): void {
  editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()
}

export const isInTable = (editor: Editor): boolean => editor.isActive('table')

export const TABLE_COMMANDS = {
  addRowBefore: (e: Editor) => e.chain().focus().addRowBefore().run(),
  addRowAfter: (e: Editor) => e.chain().focus().addRowAfter().run(),
  deleteRow: (e: Editor) => e.chain().focus().deleteRow().run(),
  addColumnBefore: (e: Editor) => e.chain().focus().addColumnBefore().run(),
  addColumnAfter: (e: Editor) => e.chain().focus().addColumnAfter().run(),
  deleteColumn: (e: Editor) => e.chain().focus().deleteColumn().run(),
  mergeCells: (e: Editor) => e.chain().focus().mergeCells().run(),
  splitCell: (e: Editor) => e.chain().focus().splitCell().run(),
  toggleHeaderRow: (e: Editor) => e.chain().focus().toggleHeaderRow().run(),
  deleteTable: (e: Editor) => e.chain().focus().deleteTable().run(),
}
