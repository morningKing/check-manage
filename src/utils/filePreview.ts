/**
 * 文件预览类型判定（按扩展名）。
 * 供 FilePreviewDialog（选渲染器）与数据页（决定文件名点击是「预览」还是「下载」）共用。
 */
import type { Component } from 'vue'
import {
  Document, Picture, Grid, Tickets, Folder, Monitor, VideoCamera, Headset,
} from '@element-plus/icons-vue'

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

/** 按文件类型给一个带色小图标，便于一眼识别文件类型。 */
export function fileTypeIcon(name?: string | null): { icon: Component; color: string } {
  const e = (name || '').split('.').pop()?.toLowerCase() || ''
  if (['xlsx', 'xls', 'csv'].includes(e)) return { icon: Grid, color: '#21a366' }       // Excel 绿
  if (['docx', 'doc'].includes(e)) return { icon: Document, color: '#2b579a' }           // Word 蓝
  if (['pptx', 'ppt'].includes(e)) return { icon: Monitor, color: '#d24726' }            // PPT 橙红
  if (e === 'pdf') return { icon: Document, color: '#e53935' }                           // PDF 红
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(e)) return { icon: Picture, color: '#8e44ad' }
  if (['md', 'markdown', 'txt', 'log', 'json', 'xml', 'yaml', 'yml', 'ini', 'conf'].includes(e)) return { icon: Tickets, color: '#607d8b' }
  if (['zip', 'rar', '7z', 'gz', 'tar'].includes(e)) return { icon: Folder, color: '#f39c12' }
  if (['mp4', 'mov', 'avi', 'mkv', 'webm'].includes(e)) return { icon: VideoCamera, color: '#3498db' }
  if (['mp3', 'wav', 'flac', 'aac', 'ogg'].includes(e)) return { icon: Headset, color: '#16a085' }
  return { icon: Document, color: '#909399' }
}
