import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { get, put, del } from '@/utils/request'
import { getRecordRelations, updateFieldRelations, deleteRecordRelations } from '../relation'

const mockGet = vi.mocked(get)
const mockPut = vi.mocked(put)
const mockDel = vi.mocked(del)

describe('Relation API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getRecordRelations 调用 GET /relations/{collection}/{recordId}', async () => {
    const relations = { field1: ['id-1', 'id-2'] }
    mockGet.mockResolvedValueOnce(relations as any)

    const res = await getRecordRelations('testCol', 'rec-1')
    expect(mockGet).toHaveBeenCalledWith('/relations/testCol/rec-1')
    expect(res).toEqual(relations)
  })

  it('updateFieldRelations 调用 PUT /relations/{collection}/{recordId}/{fieldName}', async () => {
    mockPut.mockResolvedValueOnce({ ids: ['id-1'] } as any)

    await updateFieldRelations('col', 'rec-1', 'refField', 'targetCol', 'targetField', ['id-1'])
    expect(mockPut).toHaveBeenCalledWith('/relations/col/rec-1/refField', {
      targetCollection: 'targetCol',
      targetField: 'targetField',
      ids: ['id-1'],
    })
  })

  it('deleteRecordRelations 调用 DELETE /relations/{collection}/{recordId}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deleteRecordRelations('col', 'rec-1')
    expect(mockDel).toHaveBeenCalledWith('/relations/col/rec-1')
  })
})
