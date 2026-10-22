import {
  AlignmentType,
  BorderStyle,
  Document,
  ExternalHyperlink,
  HeadingLevel,
  Packer,
  Paragraph,
  TextRun,
  type IRunOptions,
} from 'docx'
import type { JSONContent } from '@tiptap/core'

export interface ExportOptions {
  title: string
  fetchImage: (canonical: string) => Promise<{ data: ArrayBuffer; type: string }>
}

const HEADING = {
  1: HeadingLevel.HEADING_1,
  2: HeadingLevel.HEADING_2,
  3: HeadingLevel.HEADING_3,
  4: HeadingLevel.HEADING_4,
  5: HeadingLevel.HEADING_5,
  6: HeadingLevel.HEADING_6,
} as const

const ALIGN = {
  left: AlignmentType.LEFT,
  center: AlignmentType.CENTER,
  right: AlignmentType.RIGHT,
  justify: AlignmentType.JUSTIFIED,
} as const

function markRun(text: string, marks: JSONContent['marks'] = []): TextRun {
  const opts: Record<string, unknown> = { text }
  for (const mark of marks) {
    switch (mark.type) {
      case 'bold': opts.bold = true; break
      case 'italic': opts.italics = true; break
      case 'underline': opts.underline = {}; break
      case 'strike': opts.strike = true; break
      case 'code':
        opts.font = 'Consolas'
        opts.shading = { fill: 'F3F4F6' }
        break
      case 'color':
        opts.color = String(mark.attrs?.color ?? '000000').replace('#', '')
        break
      case 'highlight':
        opts.shading = { fill: String(mark.attrs?.color ?? '#fff3a3').replace('#', '') }
        break
      case 'textStyle': {
        const size = String(mark.attrs?.fontSize ?? '')
        if (size) opts.size = Math.round(parseFloat(size) * 2)
        break
      }
      default:
        break
    }
  }
  return new TextRun(opts as IRunOptions)
}

function inlineToRuns(nodes: JSONContent[] = []): Array<TextRun | ExternalHyperlink> {
  const out: Array<TextRun | ExternalHyperlink> = []
  for (const node of nodes) {
    if (node.type === 'hardBreak') {
      out.push(new TextRun({ break: 1 }))
      continue
    }
    if (node.type !== 'text') continue
    const link = node.marks?.find((mark) => mark.type === 'link')
    const href = String(link?.attrs?.href ?? '')
    const safe = /^(https?:\/\/|mailto:)/i.test(href)
    const run = markRun(node.text ?? '', node.marks ?? [])
    out.push(safe && link ? new ExternalHyperlink({ children: [run], link: href }) : run)
  }
  return out
}

function paragraphFrom(node: JSONContent): Paragraph {
  const align = node.attrs?.textAlign
  return new Paragraph({
    children: inlineToRuns(node.content),
    alignment: align && ALIGN[align as keyof typeof ALIGN]
      ? ALIGN[align as keyof typeof ALIGN]
      : undefined,
  })
}

function listParagraphs(item: JSONContent, bullet: boolean, level = 0): Paragraph[] {
  return (item.content ?? []).flatMap((child) => {
    if (child.type === 'paragraph') {
      return [new Paragraph({
        children: inlineToRuns(child.content),
        bullet: bullet ? { level } : undefined,
        numbering: bullet ? undefined : { reference: 'ordered', level },
      })]
    }
    return blockToParagraphs(child)
  })
}

export function blockToParagraphs(node: JSONContent): Paragraph[] {
  switch (node.type) {
    case 'heading':
      return [new Paragraph({
        children: inlineToRuns(node.content),
        heading: HEADING[(node.attrs?.level ?? 1) as keyof typeof HEADING] ?? HeadingLevel.HEADING_1,
        alignment: ALIGN[(node.attrs?.textAlign ?? 'left') as keyof typeof ALIGN],
      })]
    case 'paragraph':
      return [paragraphFrom(node)]
    case 'bulletList':
      return (node.content ?? []).flatMap((item) => listParagraphs(item, true))
    case 'orderedList':
      return (node.content ?? []).flatMap((item) => listParagraphs(item, false))
    case 'taskList':
      return (node.content ?? []).flatMap((item) => {
        const checked = item.attrs?.checked === true
        return (item.content ?? []).flatMap((child) => {
          if (child.type !== 'paragraph') return blockToParagraphs(child)
          return [new Paragraph({
            children: [new TextRun(checked ? '☑ ' : '☐ '), ...inlineToRuns(child.content)],
            bullet: { level: 0 },
          })]
        })
      })
    case 'blockquote':
      return (node.content ?? []).flatMap((child) => {
        if (child.type !== 'paragraph') return blockToParagraphs(child)
        return [new Paragraph({ children: inlineToRuns(child.content), indent: { left: 480 } })]
      })
    case 'codeBlock':
      return [new Paragraph({
        children: [new TextRun({
          text: (node.content ?? []).map((child) => child.text ?? '').join(''),
          font: 'Consolas',
        })],
        shading: { fill: 'F3F4F6' },
      })]
    case 'horizontalRule':
      return [new Paragraph({
        border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: '999999' } },
      })]
    default:
      return []
  }
}

export async function exportDocx(json: JSONContent, options: ExportOptions): Promise<Blob> {
  void options // 图片映射在 Task 12 接入；核心导出先用纯文本块
  const paragraphs = (json.content ?? []).flatMap(blockToParagraphs)
  const doc = new Document({
    numbering: {
      config: [{
        reference: 'ordered',
        levels: [{ level: 0, format: 'decimal', text: '%1.', alignment: AlignmentType.LEFT }],
      }],
    },
    sections: [{
      properties: { page: { size: { width: 11906, height: 16838 } } },
      children: paragraphs,
    }],
  })
  return Packer.toBlob(doc)
}

export function downloadDocx(blob: Blob, title: string): void {
  const safe = (title || '未命名文档').replace(/[\\/:*?"<>|]/g, '_')
  const date = new Date().toISOString().slice(0, 10).replace(/-/g, '')
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${safe}-${date}.docx`
  anchor.click()
  URL.revokeObjectURL(url)
}
