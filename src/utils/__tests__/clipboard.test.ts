import { describe, it, expect, vi, afterEach } from 'vitest'
import { copyText } from '../clipboard'

// jsdom doesn't implement document.execCommand, so install a mock we can assert
// on (and remove it afterwards). Assignment works whether or not it pre-exists.
function mockExecCommand(result: boolean) {
  const fn = vi.fn().mockReturnValue(result)
  ;(document as unknown as { execCommand: unknown }).execCommand = fn
  return fn
}

describe('copyText', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    delete (document as unknown as { execCommand?: unknown }).execCommand
  })

  it('uses the Clipboard API in a secure context', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('isSecureContext', true)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    expect(await copyText('hi')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('hi')
  })

  it('falls back to execCommand on a non-secure context (LAN IP / http)', async () => {
    vi.stubGlobal('isSecureContext', false)
    vi.stubGlobal('navigator', {})
    const exec = mockExecCommand(true)
    expect(await copyText('hi')).toBe(true)
    expect(exec).toHaveBeenCalledWith('copy')
  })

  it('falls back when the Clipboard API throws', async () => {
    vi.stubGlobal('isSecureContext', true)
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    const exec = mockExecCommand(true)
    expect(await copyText('hi')).toBe(true)
    expect(exec).toHaveBeenCalled()
  })

  it('returns false when both paths fail', async () => {
    vi.stubGlobal('isSecureContext', false)
    vi.stubGlobal('navigator', {})
    mockExecCommand(false)
    expect(await copyText('hi')).toBe(false)
  })
})
