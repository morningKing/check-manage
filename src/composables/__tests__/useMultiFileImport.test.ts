import { describe, it, expect, vi } from 'vitest'
import { useMultiFileImport } from '../useMultiFileImport'
import type { ImportPageResult } from '@/utils/importPageRecords'

function file(name: string): File {
  return new File(['x'], name)
}

function okResult(overrides: Partial<ImportPageResult> = {}): ImportPageResult {
  return { success: 1, failed: 0, created: 1, updated: 0, failures: [], ...overrides }
}

describe('useMultiFileImport', () => {
  it('processes files strictly in sequence (file 2 does not start before file 1 finishes uploading)', async () => {
    const events: string[] = []
    const parseFile = vi.fn(async (f: File) => {
      events.push(`parse:${f.name}`)
      return [{ n: 1 }]
    })
    const uploadRecords = vi.fn(async () => {
      events.push('upload:start')
      await new Promise((r) => setTimeout(r, 5))
      events.push('upload:end')
      return okResult()
    })
    const retryFailures = vi.fn()

    const m = useMultiFileImport({ parseFile, uploadRecords, retryFailures })
    m.setFiles([file('a.xlsx'), file('b.xlsx')])
    await m.start()

    expect(events).toEqual([
      'parse:a.xlsx', 'upload:start', 'upload:end',
      'parse:b.xlsx', 'upload:start', 'upload:end',
    ])
    expect(m.state.fileResults.map((r) => r.status)).toEqual(['success', 'success'])
  })

  it('skips a file that fails to parse and continues with the rest', async () => {
    const parseFile = vi.fn(async (f: File) => {
      if (f.name === 'bad.xlsx') throw new Error('文件已损坏')
      return [{ n: 1 }]
    })
    const uploadRecords = vi.fn(async () => okResult())
    const retryFailures = vi.fn()

    const m = useMultiFileImport({ parseFile, uploadRecords, retryFailures })
    m.setFiles([file('bad.xlsx'), file('good.xlsx')])
    await m.start()

    expect(m.state.fileResults[0].status).toBe('failed')
    expect(m.state.fileResults[0].errorMessage).toBe('文件已损坏')
    expect(m.state.fileResults[1].status).toBe('success')
    expect(uploadRecords).toHaveBeenCalledTimes(1)
  })

  it('stops remaining files after a mid-way cancel, marking them skipped', async () => {
    const parseFile = vi.fn(async () => [{ n: 1 }])
    const retryFailures = vi.fn()
    const uploadRecords = vi.fn(async () => {
      // 模拟用户在第一个文件上传过程中点击取消
      m.cancel()
      return okResult()
    })

    const m = useMultiFileImport({ parseFile, uploadRecords, retryFailures })
    m.setFiles([file('a.xlsx'), file('b.xlsx'), file('c.xlsx')])
    await m.start()

    expect(m.state.fileResults[0].status).toBe('success')
    expect(m.state.fileResults[1].status).toBe('skipped')
    expect(m.state.fileResults[2].status).toBe('skipped')
    expect(parseFile).toHaveBeenCalledTimes(1)
  })

  it("keeps per-file results isolated: one file's failures do not affect another file's result", async () => {
    const parseFile = vi.fn(async () => [{ n: 1 }])
    const uploadRecords = vi.fn()
      .mockResolvedValueOnce(okResult({
        failed: 1,
        failures: [{ originalRecord: {}, payload: { id: '1', data: {}, relations: {} }, reason: 'x' }],
      }))
      .mockResolvedValueOnce(okResult())
    const retryFailures = vi.fn()

    const m = useMultiFileImport({ parseFile, uploadRecords, retryFailures })
    m.setFiles([file('a.xlsx'), file('b.xlsx')])
    await m.start()

    expect(m.state.fileResults[0].status).toBe('partial')
    expect(m.state.fileResults[0].result?.failed).toBe(1)
    expect(m.state.fileResults[1].status).toBe('success')
    expect(m.state.fileResults[1].result?.failed).toBe(0)
  })

  it('removeStagedFile removes the file from the staged list before start', () => {
    const m = useMultiFileImport({ parseFile: vi.fn(), uploadRecords: vi.fn(), retryFailures: vi.fn() })

    m.setFiles([file('a.xlsx'), file('b.xlsx')])
    m.removeStagedFile(0)

    expect(m.state.stagedFiles.map((f) => f.name)).toEqual(['b.xlsx'])
  })

  it("retryFileFailures merges retry results into that file's result and toggles retrying", async () => {
    const parseFile = vi.fn(async () => [{ n: 1 }])
    const failingFailure = { originalRecord: {}, payload: { id: '1', data: {}, relations: {} }, reason: 'boom' }
    const uploadRecords = vi.fn(async () => okResult({ success: 1, created: 1, failed: 1, failures: [failingFailure] }))
    let resolveRetry: (v: ImportPageResult) => void = () => {}
    const retryFailures = vi.fn(() => new Promise<ImportPageResult>((r) => { resolveRetry = r }))

    const m = useMultiFileImport({ parseFile, uploadRecords, retryFailures })
    m.setFiles([file('a.xlsx')])
    await m.start()
    expect(m.state.fileResults[0].status).toBe('partial')

    const retryPromise = m.retryFileFailures(0)
    expect(m.state.fileResults[0].retrying).toBe(true)
    resolveRetry(okResult({ success: 1, created: 1, failed: 0, failures: [] }))
    await retryPromise

    expect(m.state.fileResults[0].retrying).toBe(false)
    expect(m.state.fileResults[0].status).toBe('success')
    expect(m.state.fileResults[0].result?.created).toBe(2)
    expect(m.state.fileResults[0].result?.failed).toBe(0)
    expect(retryFailures).toHaveBeenCalledWith([failingFailure], expect.any(Function))
  })
})
