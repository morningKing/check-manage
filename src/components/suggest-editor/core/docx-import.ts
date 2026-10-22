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

/**
 * 可导入的图片类型：与服务端附件白名单（utils/suggest_editor_attachments.py 的
 * detect_image_mime）保持同源。文档里的其它格式（老 Word 常见的 EMF/WMF 公式图、
 * bmp、tiff…）只跳过该图并计数，绝不让整份文档导入失败。
 */
const SUPPORTED_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])

function normalizeImageType(contentType: string): string {
  const type = (contentType || '').toLowerCase()
  return type === 'image/jpg' ? 'image/jpeg' : type
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
  let skippedImages = 0
  const arrayBuffer = await file.arrayBuffer()
  const result = await mammoth.convertToHtml(
    // mammoth 的浏览器构建只认 arrayBuffer、Node 构建只认 buffer：两边都传，
    // 浏览器行为不变，单测/工具链也能跑同一入口
    { arrayBuffer, buffer: new Uint8Array(arrayBuffer) } as Parameters<typeof mammoth.convertToHtml>[0],
    {
      convertImage: mammoth.images.imgElement(async (image) => {
        const alt = (image as { altText?: string }).altText ?? ''
        const type = normalizeImageType(image.contentType)
        if (!SUPPORTED_IMAGE_TYPES.has(type)) {
          skippedImages += 1
          return { src: '', alt }
        }
        try {
          const base64 = await image.read('base64')
          const binary = atob(base64)
          const bytes = new Uint8Array(binary.length)
          for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
          const id = `img-${++index}`
          images.push({
            id,
            blob: new Blob([bytes], { type }),
            filename: `${id}.${extFromMime(type)}`,
          })
          return { src: `se-img://${id}`, alt }
        } catch {
          // 单张图数据损坏也不中断导入
          skippedImages += 1
          return { src: '', alt }
        }
      }),
    },
  )
  const sanitized = sanitizeImportedHtml(result.value)
  const warnings = [...result.messages.map((m) => m.message), ...sanitized.warnings]
  if (skippedImages > 0) warnings.push(`已跳过 ${skippedImages} 张不支持的图片（如 EMF/WMF 公式图）`)
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
    // src 为空的 img 是被跳过的不支持图片（见 parseDocx）：连同它独占的段落一起移除
    if (!img.getAttribute('src')) {
      const parent = img.parentElement
      img.remove()
      if (parent?.tagName === 'P' && parent.childElementCount === 0) parent.remove()
      return
    }
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
    if (!src.startsWith('se-img://')) return
    const url = urlById.get(src.slice('se-img://'.length))
    if (url) {
      img.setAttribute('src', url)
      return
    }
    // 占位符没有被替换（该图被跳过/上传失败）：不留死链。
    // figure 节点在编辑器 schema 里必须有 image（content: 'image figureCaption'），
    // 所以图没了就连 figure 一起移除（导入时图注恒为空，不会误删用户内容）
    const figure = img.closest('figure')
    img.remove()
    if (figure && figure.querySelector('img') === null) figure.remove()
  })
  return doc.body.innerHTML
}
