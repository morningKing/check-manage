/**
 * Data-page file/image field uploads.
 *
 * The JSONB stored in dynamic_data references a `data_files` row by uid.
 * Download URL is auth-protected (login_required_sse, same as AI chat
 * file downloads), so `authedDataFileUrl` appends the JWT via
 * ?access_token= when the URL is consumed by <img src> / <a href>
 * (those can't set an Authorization header).
 */
import { post, get } from '@/utils/request'

export interface DataFileMeta {
  id: string
  name: string
  size: number
  mimeType: string
  url: string  // bare relative path: /api/data-files/<id>/download
}

export function uploadDataFile(file: File, collection?: string, fieldName?: string): Promise<DataFileMeta> {
  const form = new FormData()
  form.append('file', file)
  // 携带目标数据页，后端据此按「该数据页的写权限」鉴权（支持被授权的访客/自定义角色）
  if (collection) form.append('collection', collection)
  // 携带字段名，后端据此查该字段的 fileConfig.allowedExtensions 做类型二次校验
  // （前端 accept/beforeUpload 只是即时反馈，真正兜底在后端，防止绕过前端直接调接口）
  if (fieldName) form.append('fieldName', fieldName)
  return post('/data-files/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function getDataFileMetadata(id: string): Promise<DataFileMeta> {
  return get(`/data-files/${encodeURIComponent(id)}`)
}

/**
 * Wrap a `/api/data-files/<id>/download` URL with the JWT so it works
 * inside `<img src>` and `<a href>` (where setting Authorization is
 * impossible). The token comes from the same localStorage key that the
 * axios interceptor uses.
 */
export function authedDataFileUrl(url: string): string {
  if (!url) return url
  try {
    const raw = localStorage.getItem('check-manage:token')
    const token = raw ? JSON.parse(raw) : ''
    if (!token) return url
    const sep = url.includes('?') ? '&' : '?'
    return `${url}${sep}access_token=${encodeURIComponent(token)}`
  } catch {
    return url
  }
}
