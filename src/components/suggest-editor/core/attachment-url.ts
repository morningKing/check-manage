import { authParam } from '@/utils/request'

const CANONICAL_PREFIX = '/suggest-editor/attachments/'

/** 文档里存 canonical URL；渲染时拼 /api 与当前 token（token 不写进文档） */
export function attachmentSrc(canonical: string): string {
  if (!canonical.startsWith(CANONICAL_PREFIX)) return canonical
  return `/api${canonical}${authParam()}`
}

/** 导出/上传等需要自己带 Authorization 头的场景：只拼 /api，不拼 token */
export function attachmentFetchPath(canonical: string): string {
  return canonical.startsWith(CANONICAL_PREFIX) ? `/api${canonical}` : canonical
}
