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
import service from '@/utils/request'
import {
  getBackups,
  createBackup,
  deleteBackup,
  restoreBackup,
  getBackupSettings,
  diffBackupCollection,
  getBackupTables,
  updateBackupSettings,
  downloadBackup,
  uploadAndRestore,
} from '../backup'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockDel = vi.mocked(del)
const mockServicePost = vi.mocked(service.post)

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

  it('createBackup 不传参数时发送空对象', async () => {
    mockPost.mockResolvedValueOnce({ id: 'bk-2' } as any)

    await createBackup()
    expect(mockPost).toHaveBeenCalledWith('/backups', {})
  })

  it('createBackup 传 note 时发送 { note }', async () => {
    mockPost.mockResolvedValueOnce({ id: 'bk-3' } as any)

    await createBackup('手动备份')
    expect(mockPost).toHaveBeenCalledWith('/backups', { note: '手动备份' })
  })

  it('createBackup 传 tables 参数时发送表级备份请求', async () => {
    mockPost.mockResolvedValueOnce({
      id: 'bk-4',
      backupScope: 'partial',
      backupTables: ['menus', 'users'],
    } as any)

    const result = await createBackup(undefined, ['menus', 'users'])

    expect(mockPost).toHaveBeenCalledWith('/backups', {
      note: undefined,
      tables: ['menus', 'users'],
    })
    expect(result.backupScope).toBe('partial')
    expect(result.backupTables).toEqual(['menus', 'users'])
  })

  it('createBackup 同时传 note 和 tables', async () => {
    mockPost.mockResolvedValueOnce({ id: 'bk-5' } as any)

    await createBackup('部署前备份', ['dynamic_data', 'data_relations'])

    expect(mockPost).toHaveBeenCalledWith('/backups', {
      note: '部署前备份',
      tables: ['dynamic_data', 'data_relations'],
    })
  })

  it('deleteBackup 调用 DELETE /backups/{id}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deleteBackup('bk-1')
    expect(mockDel).toHaveBeenCalledWith('/backups/bk-1')
  })

  it('restoreBackup 调用 POST /backups/{id}/restore', async () => {
    mockServicePost.mockResolvedValueOnce({ data: { message: '还原成功' } } as any)

    const res = await restoreBackup('bk-1')
    expect(mockServicePost).toHaveBeenCalledWith('/backups/bk-1/restore', {})
    expect(res.data).toEqual({ message: '还原成功' })
  })

  it('restoreBackup 传 tables 参数时发送表级还原请求', async () => {
    mockServicePost.mockResolvedValueOnce({ data: { message: '还原成功' } } as any)

    await restoreBackup('bk-1', ['menus', 'users'])

    expect(mockServicePost).toHaveBeenCalledWith('/backups/bk-1/restore', {
      tables: ['menus', 'users'],
    })
  })

  it('getBackupSettings 调用 GET /backups/settings', async () => {
    const settings = { enabled: true, interval: 'daily' }
    mockGet.mockResolvedValueOnce(settings as any)

    const res = await getBackupSettings()
    expect(mockGet).toHaveBeenCalledWith('/backups/settings')
    expect(res).toEqual(settings)
  })

  it('diffBackupCollection 调用 POST /backups/diff', async () => {
    const diffResult = { added: [], removed: [], modified: [], unchangedCount: 5, fields: [] }
    vi.mocked(post).mockResolvedValueOnce(diffResult as any)

    const res = await diffBackupCollection('test-col', 'current', 'bk-1')
    expect(post).toHaveBeenCalledWith('/backups/diff', {
      collection: 'test-col',
      baseSource: 'current',
      targetSource: 'bk-1',
    })
    expect(res).toEqual(diffResult)
  })

  it('diffBackupCollection 两个备份之间对比', async () => {
    const diffResult = { added: [{ id: 'r1' }], removed: [], modified: [], unchangedCount: 0, fields: [] }
    vi.mocked(post).mockResolvedValueOnce(diffResult as any)

    const res = await diffBackupCollection('my-col', 'bk-1', 'bk-2')
    expect(post).toHaveBeenCalledWith('/backups/diff', {
      collection: 'my-col',
      baseSource: 'bk-1',
      targetSource: 'bk-2',
    })
    expect(res).toEqual(diffResult)
  })

  it('getBackupTables 调用 GET /backups/tables', async () => {
    const tables = [
      { name: 'menus', label: '菜单配置' },
      { name: 'users', label: '用户数据' },
    ]
    mockGet.mockResolvedValueOnce(tables as any)

    const res = await getBackupTables()
    expect(mockGet).toHaveBeenCalledWith('/backups/tables')
    expect(res).toEqual(tables)
  })

  it('updateBackupSettings 调用 PUT /backups/settings', async () => {
    const settings = { enabled: true, interval: 'weekly' as const, retentionCount: 5 }
    vi.mocked(service.put).mockResolvedValueOnce({ data: settings } as any)

    await updateBackupSettings(settings)
    expect(service.put).toHaveBeenCalledWith('/backups/settings', settings)
  })

  it('downloadBackup 调用 GET /backups/{id}/download', async () => {
    const mockBlob = new Blob(['test'])
    vi.mocked(service.get).mockResolvedValueOnce({
      data: mockBlob,
      headers: { 'content-disposition': 'filename=test.zip' },
    } as any)

    // Mock DOM APIs
    const mockLink = {
      href: '',
      download: '',
      click: vi.fn(),
    }
    vi.spyOn(document, 'createElement').mockReturnValue(mockLink as any)
    vi.spyOn(document.body, 'appendChild').mockImplementation(() => mockLink as any)
    vi.spyOn(document.body, 'removeChild').mockImplementation(() => mockLink as any)
    vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:test')
    vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => {})

    await downloadBackup('bk-1', 'test-backup')

    expect(service.get).toHaveBeenCalledWith('/backups/bk-1/download', {
      responseType: 'blob',
    })
    expect(mockLink.click).toHaveBeenCalled()
  })

  it('uploadAndRestore 调用 POST /backups/upload-restore', async () => {
    const file = new File(['test'], 'backup.zip', { type: 'application/zip' })
    mockServicePost.mockResolvedValueOnce({ data: { message: '还原成功' } } as any)

    await uploadAndRestore(file)

    expect(service.post).toHaveBeenCalledWith(
      '/backups/upload-restore',
      expect.any(FormData),
      {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 120000,
      }
    )
  })
})
