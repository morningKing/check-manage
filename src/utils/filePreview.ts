/**
 * 文件预览类型判定（按扩展名）。
 * 供 FilePreviewDialog（选渲染器）与数据页（决定文件名点击是「预览」还是「下载」）共用。
 */
export type PreviewKind =
  | 'docx' | 'excel' | 'pptx' | 'pdf'
  | 'markdown' | 'text' | 'image' | 'unsupported'

export function previewKind(name?: string | null): PreviewKind {
  const e = (name || '').split('.').pop()?.toLowerCase() || ''
  if (e === 'docx') return 'docx'
  if (['xlsx', 'xls'].includes(e)) return 'excel'
  if (e === 'pptx') return 'pptx'
  if (e === 'pdf') return 'pdf'
  if (['md', 'markdown'].includes(e)) return 'markdown'
  if (['txt', 'log', 'json', 'csv', 'xml', 'yaml', 'yml', 'ini', 'conf'].includes(e)) return 'text'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(e)) return 'image'
  return 'unsupported'
}

/** 该文件是否支持在线渲染预览；不支持的只提供下载。 */
export function isPreviewable(name?: string | null): boolean {
  return previewKind(name) !== 'unsupported'
}
