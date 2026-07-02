export function isSafeUrl(u: string): boolean {
  if (!u) return false
  if ((u.startsWith('/') && !u.startsWith('//')) || u.startsWith('./')) return true
  return /^https?:\/\//i.test(u)
}
