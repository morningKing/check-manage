/**
 * 关键词高亮工具：把文本中（大小写不敏感）出现的关键词包进 <mark>，供
 * v-html 渲染。**原文与关键词都先做 HTML 转义**，避免把用户内容里的
 * `<script>` 等变成真实标签（XSS 安全）。
 */

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 返回高亮后的 HTML 字符串：命中段落在 <mark> 内，其余为转义纯文本。
 * query 为空/纯空白时不高亮，仅返回转义原文。
 *
 * 做法：在**原文**上用带捕获组的正则 split（命中段落落在奇数下标），再对
 * 每段分别转义——这样转义引入的 `&lt;` 等实体不会影响关键词定位。
 */
export function highlightHtml(text: string, query: string): string {
  const safe = text ?? ''
  const q = (query ?? '').trim()
  if (!q) return escapeHtml(safe)
  const re = new RegExp(`(${escapeRegExp(q)})`, 'gi')
  return safe
    .split(re)
    .map((part, i) => {
      const esc = escapeHtml(part)
      return i % 2 === 1 ? `<mark>${esc}</mark>` : esc
    })
    .join('')
}
