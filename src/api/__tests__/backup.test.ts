import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}))

import { get, post, del } from '@/utils/request'
import { getBackups, createBackup, deleteBackup, restoreBackup, getBackupSettings, diffBackupCollection } from '../backup'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockDel = vi.mocked(del)

describe('Backup API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getBackups 调用 GET /backups', async () => {
    const backups = [{ id: 'bk-1', name: 'backup1' }]
    mockGet.mockResolvedValueOnce(backups as any)

    const res = await getBackups()
    expect(mockGet).toHaveBeenCalledWith('/backups')
    expect(res).toEqual(backups)
  })

  it('createBackup 不传 note 时发送空对象', async () => {
    mockPost.mockResolvedValueOnce({ id: 'bk-2' } as any)

    await createBackup()
    expect(mockPost).toHaveBeenCalledWith('/backups', {})
  })

  it('createBackup 传 note 时发送 { note }', async () => {
    mockPost.mockResolvedValueOnce({ id: 'bk-3' } as any)

    await createBackup('手动备份')
    expect(mockPost).toHaveBeenCalledWith('/backups', { note: '手动备份' })
  })

  it('deleteBackup 调用 DELETE /backups/{id}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deleteBackup('bk-1')
    expect(mockDel).toHaveBeenCalledWith('/backups/bk-1')
  })

  it('restoreBackup 调用 POST /backups/{id}/restore', async () => {
    mockPost.mockResolvedValueOnce({ message: '还原成功' } as any)

    const res = await restoreBackup('bk-1')
    expect(mockPost).toHaveBeenCalledWith('/backups/bk-1/restore')
    expect(res).toEqual({ message: '还原成功' })
  })

  it('getBackupSettings 调用 GET /backups/settings', async () => {
    const settings = { enabled: true, interval: 24 }
    mockGet.mockResolvedValueOnce(settings as any)

    const res = await getBackupSettings()
    expect(mockGet).toHaveBeenCalledWith('/backups/settings')
    expect(res).toEqual(settings)
  })

  it('diffBackupCollection 调用 POST /backups/diff', async () => {
    const diffResult = { added: [], removed: [], modified: [], unchangedCount: 5 }
    mockPost.mockResolvedValueOnce(diffResult as any)

    const res = await diffBackupCollection('test-col', 'current', 'bk-1')
    expect(mockPost).toHaveBeenCalledWith('/backups/diff', {
      collection: 'test-col',
      baseSource: 'current',
      targetSource: 'bk-1',
    })
    expect(res).toEqual(diffResult)
  })

  it('diffBackupCollection 两个备份之间对比', async () => {
    const diffResult = { added: [{ id: 'r1' }], removed: [], modified: [], unchangedCount: 0 }
    mockPost.mockResolvedValueOnce(diffResult as any)

    const res = await diffBackupCollection('my-col', 'bk-1', 'bk-2')
    expect(mockPost).toHaveBeenCalledWith('/backups/diff', {
      collection: 'my-col',
      baseSource: 'bk-1',
      targetSource: 'bk-2',
    })
    expect(res).toEqual(diffResult)
  })
})
