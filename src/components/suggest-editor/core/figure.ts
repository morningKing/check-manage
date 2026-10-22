import { Node, mergeAttributes, type Editor } from '@tiptap/core'
import { Image } from '@tiptap/extension-image'
import { attachmentSrc } from './attachment-url'

export const FigureCaption = Node.create({
  name: 'figureCaption',
  content: 'inline*',
  group: 'block',
  parseHTML: () => [{ tag: 'figcaption' }],
  renderHTML: () => ['figcaption', 0],
})

const SeImage = Image.extend({
  addNodeView() {
    return ({ node }) => {
      const img = document.createElement('img')
      const apply = (attrs: Record<string, unknown>) => {
        img.src = attachmentSrc(String(attrs.src ?? ''))
        img.alt = String(attrs.alt ?? '')
        img.className = 'se-figure-img'
      }
      apply(node.attrs)
      return {
        dom: img,
        update: (updated) => {
          if (updated.type.name !== 'image') return false
          apply(updated.attrs)
          return true
        },
      }
    }
  },
})

export const Figure = Node.create({
  name: 'figure',
  group: 'block',
  content: 'image figureCaption',
  parseHTML: () => [{ tag: 'figure' }],
  renderHTML: ({ HTMLAttributes }) =>
    ['figure', mergeAttributes(HTMLAttributes, { class: 'se-figure' }), 0],
})

export const FIGURE_EXTENSIONS = [
  SeImage.configure({ inline: false, allowBase64: false }),
  FigureCaption,
  Figure,
]

export function insertFigure(editor: Editor, attrs: { src: string; alt?: string }): void {
  editor.chain().focus().insertContent({
    type: 'figure',
    content: [
      { type: 'image', attrs: { src: attrs.src, alt: attrs.alt ?? '' } },
      { type: 'figureCaption' },
    ],
  }).run()
}
