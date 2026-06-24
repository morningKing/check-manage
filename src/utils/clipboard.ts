/**
 * Copy text to the clipboard with a non-secure-context fallback.
 *
 * `navigator.clipboard` is only available in secure contexts (HTTPS or
 * localhost). When the app is served over plain HTTP on a LAN IP (e.g. the
 * production reverse proxy at http://192.168.x.x:8080), it's undefined and the
 * old `navigator.clipboard.writeText(...)` threw → "复制失败". Fall back to the
 * legacy `document.execCommand('copy')` via a hidden textarea, which works over
 * plain HTTP.
 *
 * Returns true on success, false if both paths fail.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '0'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}
