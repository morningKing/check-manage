import { TextStyle } from '@tiptap/extension-text-style'
import { Color } from '@tiptap/extension-text-style/color'
import { FontSize } from '@tiptap/extension-text-style/font-size'
import { Highlight } from '@tiptap/extension-highlight'
import { TextAlign } from '@tiptap/extension-text-align'
import type { Editor } from '@tiptap/core'

export const FONT_SIZES = ['12pt', '14pt', '16pt', '18pt', '20pt', '24pt']
export const TEXT_COLORS = [
  '#1f2937', '#c0392b', '#d97706', '#047857',
  '#1d4ed8', '#7c3aed', '#be185d', '#6b7280',
]
export const HIGHLIGHT_COLORS = [
  '#fff3a3', '#d1fae5', '#dbeafe', '#fde68a', '#fce7f3', '#e5e7eb',
]

export const FORMATTING_EXTENSIONS = [
  TextStyle,
  Color,
  FontSize,
  Highlight.configure({ multicolor: true }),
  TextAlign.configure({ types: ['heading', 'paragraph'], alignments: ['left', 'center', 'right', 'justify'] }),
]

export function clearFormatting(editor: Editor): void {
  editor.chain().focus().unsetTextAlign().unsetAllMarks().clearNodes().run()
}
