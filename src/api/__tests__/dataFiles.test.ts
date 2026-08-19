import { describe, it, expect, beforeEach } from 'vitest'
import { authedDataFileUrl } from '../dataFiles'

describe('authedDataFileUrl', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('无 token 时原样返回 url', () => {
    expect(authedDataFileUrl('/api/data-files/x/download')).toBe('/api/data-files/x/download')
  })

  it('appends token as access_token query param', () => {
    localStorage.setItem('check-manage:token', JSON.stringify('tok-123'))
    const out = authedDataFileUrl('/api/data-files/x/download')
    expect(out).toBe('/api/data-files/x/download?access_token=tok-123')
  })

  it('url 已带查询参数时用 & 拼接', () => {
    localStorage.setItem('check-manage:token', JSON.stringify('tok-123'))
    const out = authedDataFileUrl('/api/data-files/x/download?path=a')
    expect(out).toBe('/api/data-files/x/download?path=a&access_token=tok-123')
  })

  it('幂等：url 已带 access_token= 时不重复叠加（AI 助手模块传入的 url 场景）', () => {
    localStorage.setItem('check-manage:token', JSON.stringify('tok-123'))
    const alreadyAuthed = '/api/ai/chat/sessions/s1/files/download?path=a&access_token=other-tok'
    expect(authedDataFileUrl(alreadyAuthed)).toBe(alreadyAuthed)
  })

  it('空字符串原样返回', () => {
    expect(authedDataFileUrl('')).toBe('')
  })
})
