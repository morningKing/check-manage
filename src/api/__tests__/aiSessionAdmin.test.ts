import { describe, it, expect, beforeEach } from 'vitest'
import { sessionFileDownloadUrl } from '../aiSessionAdmin'

describe('sessionFileDownloadUrl', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('带上 access_token（window.open/<a href> 拿不到 axios 拦截器加的 Authorization 头，必须靠这个）', () => {
    localStorage.setItem('check-manage:token', JSON.stringify('tok-123'))
    const url = sessionFileDownloadUrl('sess-1', 'outputs/report.pdf')
    expect(url).toBe(
      '/api/ai/chat/admin/sessions/v2/sess-1/files/download?path=outputs%2Freport.pdf&access_token=tok-123'
    )
  })

  it('对 sessionId 和 path 分别做 URL 编码', () => {
    localStorage.setItem('check-manage:token', JSON.stringify('tok'))
    const url = sessionFileDownloadUrl('sess/1', '员工名单.xlsx')
    expect(url).toContain('/sessions/v2/sess%2F1/files/download')
    expect(url).toContain(`path=${encodeURIComponent('员工名单.xlsx')}`)
  })

  it('无 token 时不带 access_token 参数（沿用 authParam 的既有行为）', () => {
    const url = sessionFileDownloadUrl('sess-1', 'a.txt')
    expect(url).toBe('/api/ai/chat/admin/sessions/v2/sess-1/files/download?path=a.txt')
  })
})
