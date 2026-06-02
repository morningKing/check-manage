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

export function uploadDataFile(file: File): Promise<DataFileMeta> {
  const form = new FormData()
  form.append('file', file)
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
