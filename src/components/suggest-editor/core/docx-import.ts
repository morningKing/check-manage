export class DocxImportError extends Error {}

export interface ImportedImage {
  id: string
  blob: Blob
  filename: string
}

export interface ImportedDocx {
  html: string
  images: ImportedImage[]
  warnings: string[]
  textLength: number
  blockCount: number
}

const ALLOWED_TAGS = new Set([
  'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'P', 'STRONG', 'B', 'EM', 'I', 'U', 'S', 'DEL',
  'A', 'UL', 'OL', 'LI', 'BLOCKQUOTE', 'PRE', 'CODE', 'BR', 'HR', 'TABLE', 'THEAD',
  'TBODY', 'TR', 'TH', 'TD', 'FIGURE', 'FIGCAPTION', 'IMG',
])
const DROP_TAGS = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META'])
const ALLOWED_ATTR = new Set(['href', 'src', 'alt', 'colspan', 'rowspan', 'title'])
export const MAX_TEXT = 2 * 1024 * 1024
export const MAX_BLOCKS = 2000
export const MAX_DOCX_BYTES = 20 * 1024 * 1024

export function assertImportLimits(textLength: number, blockCount: number): void {
  if (textLength > MAX_TEXT) throw new DocxImportError('文档文本超过 2MB 上限')
  if (blockCount > MAX_BLOCKS) throw new DocxImportError('文档块数超过 2000 上限')
}

function extFromMime(mime: string): string {
  return mime === 'image/jpeg' ? 'jpg' : mime.split('/')[1] || 'png'
}

export async function parseDocx(file: File): Promise<ImportedDocx> {
  if (!file.name.toLowerCase().endsWith('.docx')) throw new DocxImportError('仅支持 .docx 文件')
  if (file.size > MAX_DOCX_BYTES) throw new DocxImportError('docx 超过 20MB 上限')
  const mammoth = await import('mammoth')
  const images: ImportedImage[] = []
  let index = 0
  const result = await mammoth.convertToHtml(
    { arrayBuffer: await file.arrayBuffer() },
    {
      convertImage: mammoth.images.imgElement(async (image) => {
        const base64 = await image.read('base64')
        const id = `img-${++index}`
        const binary = atob(base64)
        const bytes = new Uint8Array(binary.length)
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
        images.push({
          id,
          blob: new Blob([bytes], { type: image.contentType }),
          filename: `${id}.${extFromMime(image.contentType)}`,
        })
        return { src: `se-img://${id}`, alt: (image as { altText?: string }).altText ?? '' }
      }),
    },
  )
  const sanitized = sanitizeImportedHtml(result.value)
  const warnings = [...result.messages.map((m) => m.message), ...sanitized.warnings]
  const doc = new DOMParser().parseFromString(sanitized.html, 'text/html')
  const textLength = (doc.body.textContent ?? '').length
  const blockCount = doc.body.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,tr,pre,blockquote').length
  assertImportLimits(textLength, blockCount)
  return { html: sanitized.html, images, warnings, textLength, blockCount }
}

export function sanitizeImportedHtml(html: string): { html: string; warnings: string[] } {
  const warnings: string[] = []
  const doc = new DOMParser().parseFromString(html, 'text/html')

  doc.querySelectorAll('table table').forEach((nested) => {
    const outer = nested.parentElement?.closest('table')
    if (!outer) return
    nested.remove()
    warnings.push('嵌套表格已降级为普通内容')
  })

  doc.body.querySelectorAll('img').forEach((img) => {
    const figure = doc.createElement('figure')
    const caption = doc.createElement('figcaption')
    const parent = img.parentElement
    if (parent?.tagName === 'P' && parent.childElementCount === 1) {
      parent.replaceWith(figure)
    } else {
      img.replaceWith(figure)
    }
    figure.appendChild(img)
    figure.appendChild(caption)
  })

  doc.body.querySelectorAll('*').forEach((el) => {
    if (DROP_TAGS.has(el.tagName)) {
      el.remove()
      warnings.push(`已移除不安全标签 ${el.tagName}`)
      return
    }
    if (!ALLOWED_TAGS.has(el.tagName)) {
      el.replaceWith(doc.createTextNode(el.textContent ?? ''))
      warnings.push(`不支持的标签 ${el.tagName} 已降级为文本`)
      return
    }
    for (const attr of Array.from(el.attributes)) {
      if (attr.name.startsWith('on') || !ALLOWED_ATTR.has(attr.name)) el.removeAttribute(attr.name)
    }
  })
  return { html: doc.body.innerHTML, warnings: Array.from(new Set(warnings)) }
}

export function replaceImagePlaceholders(html: string, urlById: Map<string, string>): string {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  doc.body.querySelectorAll('img').forEach((img) => {
    const src = img.getAttribute('src') ?? ''
    const id = src.startsWith('se-img://') ? src.slice('se-img://'.length) : ''
    const url = urlById.get(id)
    if (url) img.setAttribute('src', url)
  })
  return doc.body.innerHTML
}
