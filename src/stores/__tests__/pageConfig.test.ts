/**
 * 页面配置 Store 单元测试
 *
 * 测试 autoSequence / autoTimestamp 辅助函数和序列值生成逻辑。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

vi.mock('@/api/relation', () => ({
  getRecordRelations: vi.fn(),
  getCollectionRelations: vi.fn(),
  updateFieldRelations: vi.fn(),
}))

vi.mock('uuid', () => {
  let counter = 0
  return {
    v4: () => `mock-uuid-${String(++counter).padStart(4, '0')}`,
  }
})

import { usePageConfigStore } from '../pageConfig'
import { get, post } from '@/utils/request'
import { getCollectionRelations, getRecordRelations } from '@/api/relation'
import type { PageConfig, FieldConfig } from '@/types'

function makeField(overrides: Partial<FieldConfig>): FieldConfig {
  return {
    id: 'field-1',
    fieldName: 'f1',
    label: '字段1',
    controlType: 'text',
    order: 0,
    required: false,
    ...overrides,
  } as FieldConfig
}

function makePageConfig(overrides: Partial<PageConfig> = {}): PageConfig {
  return {
    id: 'page-test',
    name: '测试页',
    description: '',
    apiEndpoint: '/test',
    fields: [],
    createdAt: '',
    updatedAt: '',
    ...overrides,
  } as PageConfig
}

describe('PageConfig Store — autoSequence', () => {
  let store: ReturnType<typeof usePageConfigStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = usePageConfigStore()
  })

  describe('getAutoSequenceFields', () => {
    it('返回 autoSequence 类型的字段', () => {
      // 直接设置 store 内部 state
      store.$patch({
        pageConfigs: [
          makePageConfig({
            id: 'page-test',
            fields: [
              makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
              makeField({ id: 'f2', fieldName: 'seq', controlType: 'autoSequence', sequenceConfig: { prefix: 'IC-', max: 999 } }),
              makeField({ id: 'f3', fieldName: 'ts', controlType: 'autoTimestamp' }),
            ],
          }),
        ],
      })

      // getAutoSequenceFields 不直接暴露，通过 getPageFields 和过滤验证
      const fields = store.getPageFields('page-test')
      const seqFields = fields.filter(f => f.controlType === 'autoSequence')
      expect(seqFields).toHaveLength(1)
      expect(seqFields[0].fieldName).toBe('seq')
    })

    it('页面不存在时返回空', () => {
      const fields = store.getPageFields('page-nonexistent')
      expect(fields).toEqual([])
    })
  })

  describe('getAutoTimestampFields', () => {
    it('返回 autoTimestamp 类型的字段', () => {
      store.$patch({
        pageConfigs: [
          makePageConfig({
            id: 'page-test',
            fields: [
              makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
              makeField({ id: 'f2', fieldName: 'ts', controlType: 'autoTimestamp' }),
            ],
          }),
        ],
      })

      const fields = store.getPageFields('page-test')
      const tsFields = fields.filter(f => f.controlType === 'autoTimestamp')
      expect(tsFields).toHaveLength(1)
      expect(tsFields[0].fieldName).toBe('ts')
    })
  })

  describe('generateNextSequenceValue', () => {
    it('无已有记录时返回第一个序列值', () => {
      store.$patch({
        pageConfigs: [
          makePageConfig({
            id: 'page-demo',
            fields: [
              makeField({
                id: 'f-seq',
                fieldName: 'seqNo',
                controlType: 'autoSequence',
                sequenceConfig: { prefix: 'IC-', max: 999 },
              }),
            ],
          }),
        ],
      })
      // pageDataCache 为空

      const field = store.getPageFields('page-demo').find(f => f.controlType === 'autoSequence')!
      // 直接调用 store 暴露的 generateNextSequenceValue
      const val = store.generateNextSequenceValue('page-demo', field)
      expect(val).toBe('IC-001')
    })

    it('从已有记录中找到最大值并递增', () => {
      store.$patch({
        pageConfigs: [
          makePageConfig({
            id: 'page-demo',
            fields: [
              makeField({
                id: 'f-seq',
                fieldName: 'seqNo',
                controlType: 'autoSequence',
                sequenceConfig: { prefix: 'IC-', max: 999 },
              }),
            ],
          }),
        ],
      })

      // 模拟缓存数据
      store.pageDataCache = {
        'page-demo': [
          { id: 'r1', seqNo: 'IC-001' },
          { id: 'r2', seqNo: 'IC-005' },
          { id: 'r3', seqNo: 'IC-003' },
        ],
      }

      const field = store.getPageFields('page-demo').find(f => f.controlType === 'autoSequence')!
      const val = store.generateNextSequenceValue('page-demo', field)
      expect(val).toBe('IC-006')
    })

    it('不同前缀的记录不干扰计数', () => {
      store.$patch({
        pageConfigs: [
          makePageConfig({
            id: 'page-demo',
            fields: [
              makeField({
                id: 'f-seq',
                fieldName: 'seqNo',
                controlType: 'autoSequence',
                sequenceConfig: { prefix: 'RM-', max: 99 },
              }),
            ],
          }),
        ],
      })
      store.pageDataCache = {
        'page-demo': [
          { id: 'r1', seqNo: 'IC-010' },
          { id: 'r2', seqNo: 'RM-03' },
        ],
      }

      const field = store.getPageFields('page-demo').find(f => f.controlType === 'autoSequence')!
      const val = store.generateNextSequenceValue('page-demo', field)
      expect(val).toBe('RM-04')
    })

    it('max 决定补零位数', () => {
      store.$patch({
        pageConfigs: [
          makePageConfig({
            id: 'page-demo',
            fields: [
              makeField({
                id: 'f-seq',
                fieldName: 'seqNo',
                controlType: 'autoSequence',
                sequenceConfig: { prefix: 'T-', max: 99999 },
              }),
            ],
          }),
        ],
      })
      store.pageDataCache = {
        'page-demo': [
          { id: 'r1', seqNo: 'T-00042' },
        ],
      }

      const field = store.getPageFields('page-demo').find(f => f.controlType === 'autoSequence')!
      const val = store.generateNextSequenceValue('page-demo', field)
      expect(val).toBe('T-00043')
    })

    it('无 sequenceConfig 时使用默认值', () => {
      store.$patch({
        pageConfigs: [
          makePageConfig({
            id: 'page-demo',
            fields: [
              makeField({
                id: 'f-seq',
                fieldName: 'seqNo',
                controlType: 'autoSequence',
                // 不设置 sequenceConfig
              }),
            ],
          }),
        ],
      })

      const field = store.getPageFields('page-demo').find(f => f.controlType === 'autoSequence')!
      const val = store.generateNextSequenceValue('page-demo', field)
      // 默认 prefix='', max=999 → padLen=3
      expect(val).toBe('001')
    })

    it('跳过非数字后缀的记录', () => {
      store.$patch({
        pageConfigs: [
          makePageConfig({
            id: 'page-demo',
            fields: [
              makeField({
                id: 'f-seq',
                fieldName: 'seqNo',
                controlType: 'autoSequence',
                sequenceConfig: { prefix: 'IC-', max: 999 },
              }),
            ],
          }),
        ],
      })
      store.pageDataCache = {
        'page-demo': [
          { id: 'r1', seqNo: 'IC-abc' },
          { id: 'r2', seqNo: 'IC-002' },
        ],
      }

      const field = store.getPageFields('page-demo').find(f => f.controlType === 'autoSequence')!
      const val = store.generateNextSequenceValue('page-demo', field)
      expect(val).toBe('IC-003')
    })
  })
})

describe('PageConfig Store — quoteSelect', () => {
  let store: ReturnType<typeof usePageConfigStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = usePageConfigStore()
  })

  it('quoteSelect 字段不会被 stripRelationFields 剥离', () => {
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
            makeField({ id: 'f2', fieldName: 'quotedCases', controlType: 'quoteSelect' }),
            makeField({ id: 'f3', fieldName: 'relatedItems', controlType: 'relation' }),
          ],
        }),
      ],
    })

    const formData = { name: '测试', quotedCases: ['id-1', 'id-2'], relatedItems: ['id-3'] }
    const result = store.stripRelationFields('page-test', formData)
    expect(result.name).toBe('测试')
    expect(result.quotedCases).toEqual(['id-1', 'id-2'])
    expect(result.relatedItems).toBeUndefined()
  })

  it('resolveQuoteLabels 正确解析标签', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
      ],
    })

    // fetchPageData('page-test') calls:
    // 1. get('/test') — main data
    // 2. get('/cases') — resolveQuoteLabels target records
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'r1', quotedCases: ['case-1', 'case-2'] },
        { id: 'r2', quotedCases: [] },
      ],
      total: 2,
    })
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseName: '用例A' },
        { id: 'case-2', caseName: '用例B' },
      ],
      total: 2,
    })

    const result = await store.fetchPageData('page-test')
    const labels = result.data[0]?.[`_quote_quotedCases_labels`]
    expect(labels).toHaveLength(2)
    expect(labels[0].label).toBe('用例A')
    expect(labels[1].label).toBe('用例B')
  })

  it('resolveQuoteLabels 保持引用数组的原始顺序', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
      ],
    })

    // 引用顺序为 case-3, case-1, case-2（非自然排序）
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'r1', quotedCases: ['case-3', 'case-1', 'case-2'] },
      ],
      total: 1,
    })
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseName: '用例A' },
        { id: 'case-2', caseName: '用例B' },
        { id: 'case-3', caseName: '用例C' },
      ],
      total: 3,
    })

    const result = await store.fetchPageData('page-test')
    const labels = result.data[0]?.[`_quote_quotedCases_labels`]
    expect(labels).toHaveLength(3)
    // 验证标签顺序与原始 ID 数组一致，而非按目标集合顺序
    expect(labels[0]).toEqual({ id: 'case-3', label: '用例C' })
    expect(labels[1]).toEqual({ id: 'case-1', label: '用例A' })
    expect(labels[2]).toEqual({ id: 'case-2', label: '用例B' })
  })

  it('resolveQuoteLabels 删除中间引用后保持剩余顺序', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
      ],
    })

    // 原始引用 B, C, D → 目标集合中 C 已被删除
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'r1', quotedCases: ['id-B', 'id-C', 'id-D'] },
      ],
      total: 1,
    })
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'id-B', caseName: '记录B' },
        // id-C 已删除，不在目标集合中
        { id: 'id-D', caseName: '记录D' },
      ],
      total: 2,
    })

    const result = await store.fetchPageData('page-test')
    const labels = result.data[0]?.[`_quote_quotedCases_labels`]
    expect(labels).toHaveLength(3)
    // B 和 D 保持原始顺序，C 的 ID 作为 fallback 显示
    expect(labels[0]).toEqual({ id: 'id-B', label: '记录B' })
    expect(labels[1]).toEqual({ id: 'id-C', label: 'id-C' }) // 已删除，fallback 为 ID
    expect(labels[2]).toEqual({ id: 'id-D', label: '记录D' })
  })

  it('fetchQuoteDisplayMaps 构建正确映射', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-001' },
        { id: 'case-2', caseId: 'IC-002' },
      ],
      total: 2,
    })

    const maps = await store.fetchQuoteDisplayMaps('page-test')
    expect(maps.quotedCases).toBeDefined()
    expect(maps.quotedCases.get('case-1')).toBe('IC-001')
    expect(maps.quotedCases.get('case-2')).toBe('IC-002')
  })

  it('fetchQuoteDisplayMaps 目标无主键时回退 displayField', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1', fieldName: 'quotedCases', controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'nm', fieldName: 'caseName', controlType: 'text' }),
          ],
        }),
      ],
    })
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseName: '苹果' },
        { id: 'case-2', caseName: '香蕉' },
      ],
      total: 2,
    })
    const maps = await store.fetchQuoteDisplayMaps('page-test')
    expect(maps.quotedCases).toBeDefined()
    expect(maps.quotedCases.get('case-1')).toBe('苹果')
    expect(maps.quotedCases.get('case-2')).toBe('香蕉')
  })

  it('fetchRelationDisplayMaps 目标无主键时回退 displayField', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1', fieldName: 'relCases', controlType: 'relation',
              relationConfig: { targetCollection: 'cases', targetField: 'back', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'nm', fieldName: 'caseName', controlType: 'text' }),
          ],
        }),
      ],
    })
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseName: '苹果' },
        { id: 'case-2', caseName: '香蕉' },
      ],
      total: 2,
    })
    const maps = await store.fetchRelationDisplayMaps('page-test')
    expect(maps.relCases).toBeDefined()
    expect(maps.relCases.get('case-1')).toBe('苹果')
    expect(maps.relCases.get('case-2')).toBe('香蕉')
  })

  it('fetchRelationDisplayMaps 目标有主键时仍用主键值', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1', fieldName: 'relCases', controlType: 'relation',
              relationConfig: { targetCollection: 'cases', targetField: 'back', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-001', caseName: '苹果' },
        { id: 'case-2', caseId: 'IC-002', caseName: '香蕉' },
      ],
      total: 2,
    })
    const maps = await store.fetchRelationDisplayMaps('page-test')
    expect(maps.relCases.get('case-1')).toBe('IC-001')
    expect(maps.relCases.get('case-2')).toBe('IC-002')
  })

  it('resolveQuoteImportValues 正确解析导入值', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-001' },
        { id: 'case-2', caseId: 'IC-002' },
      ],
      total: 2,
    })

    const records = [
      { quotedCases: ['IC-001', 'IC-002'] },
      { quotedCases: ['case-1'] },  // 已经是内部 ID
    ]

    await store.resolveQuoteImportValues('page-test', records)
    expect(records[0].quotedCases).toEqual(['case-1', 'case-2'])
    expect(records[1].quotedCases).toEqual(['case-1'])
  })

  it('resolveQuoteImportValues 保持导入值的原始顺序', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-001' },
        { id: 'case-2', caseId: 'IC-002' },
        { id: 'case-3', caseId: 'IC-003' },
      ],
      total: 3,
    })

    // 导入顺序为 IC-003, IC-001, IC-002（非自然排序）
    const records = [
      { quotedCases: ['IC-003', 'IC-001', 'IC-002'] },
    ]

    await store.resolveQuoteImportValues('page-test', records)
    // 验证解析后的 ID 保持与导入值相同的顺序
    expect(records[0].quotedCases).toEqual(['case-3', 'case-1', 'case-2'])
  })

  it('resolveQuoteImportValues 跳过不存在的值但保持剩余顺序', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-001' },
        { id: 'case-3', caseId: 'IC-003' },
      ],
      total: 2,
    })

    // IC-002 不存在，应被过滤但不影响 IC-001 和 IC-003 的顺序
    const records = [
      { quotedCases: ['IC-001', 'IC-002', 'IC-003'] },
    ]

    await store.resolveQuoteImportValues('page-test', records)
    expect(records[0].quotedCases).toEqual(['case-1', 'case-3'])
  })

  it('resolveQuoteImportValues 通过 displayField 名称匹配', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-001', caseName: '用例A' },
        { id: 'case-2', caseId: 'IC-002', caseName: '用例B' },
      ],
      total: 2,
    })

    // 用户在 Excel 中填写的是 displayField 的值（下拉框中看到的名称）
    const records = [
      { quotedCases: ['用例A', '用例B'] },
    ]

    await store.resolveQuoteImportValues('page-test', records)
    expect(records[0].quotedCases).toEqual(['case-1', 'case-2'])
  })

  it('resolveQuoteImportValues 主键匹配优先于 displayField', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    // caseId='X' 的记录和 caseName='X' 的记录不同
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'X', caseName: '用例1' },
        { id: 'case-2', caseId: 'Y', caseName: 'X' },
      ],
      total: 2,
    })

    const records = [{ quotedCases: ['X'] }]
    await store.resolveQuoteImportValues('page-test', records)
    // 主键匹配优先
    expect(records[0].quotedCases).toEqual(['case-1'])
  })

  it('resolveQuoteImportValues 相同 displayField 值匹配所有记录', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    // 三条记录的 caseName 相同
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-001', caseName: '同名用例' },
        { id: 'case-2', caseId: 'IC-002', caseName: '同名用例' },
        { id: 'case-3', caseId: 'IC-003', caseName: '同名用例' },
      ],
      total: 3,
    })

    const records = [{ quotedCases: ['同名用例'] }]
    await store.resolveQuoteImportValues('page-test', records)
    // 应匹配所有三条记录
    expect(records[0].quotedCases).toEqual(['case-1', 'case-2', 'case-3'])
  })

  it('resolveQuoteImportValues 相同主键值匹配所有记录', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    // 两条记录的 caseId 相同
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-DUP', caseName: '用例A' },
        { id: 'case-2', caseId: 'IC-DUP', caseName: '用例B' },
      ],
      total: 2,
    })

    const records = [{ quotedCases: ['IC-DUP'] }]
    await store.resolveQuoteImportValues('page-test', records)
    expect(records[0].quotedCases).toEqual(['case-1', 'case-2'])
  })

  it('resolveQuoteImportValues 重复导入值去重', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-001', caseName: '机房安全检查' },
        { id: 'case-2', caseId: 'IC-002', caseName: '机房安全检查' },
      ],
      total: 2,
    })

    // 导入值中同名出现两次，解析后应去重，只保留 2 条而非 4 条
    const records = [{ quotedCases: ['机房安全检查', '机房安全检查'] }]
    await store.resolveQuoteImportValues('page-test', records)
    expect(records[0].quotedCases).toEqual(['case-1', 'case-2'])
  })

  it('stripRelationFields 保持 quoteSelect 数组顺序不变', () => {
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
            makeField({ id: 'f2', fieldName: 'quotedCases', controlType: 'quoteSelect' }),
            makeField({ id: 'f3', fieldName: 'relatedItems', controlType: 'relation' }),
          ],
        }),
      ],
    })

    const ordered = ['id-3', 'id-1', 'id-2']
    const formData = { name: '测试', quotedCases: ordered, relatedItems: ['id-x'] }
    const result = store.stripRelationFields('page-test', formData)
    expect(result.quotedCases).toEqual(['id-3', 'id-1', 'id-2'])
  })

  it('fetchQuoteDisplayMaps 导出映射按 ID 数组顺序生成', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'quotedCases',
              controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'case-1', caseId: 'IC-001' },
        { id: 'case-2', caseId: 'IC-002' },
      ],
      total: 2,
    })

    const maps = await store.fetchQuoteDisplayMaps('page-test')
    expect(maps.quotedCases).toBeDefined()
    expect(maps.quotedCases.get('case-1')).toBe('IC-001')
    expect(maps.quotedCases.get('case-2')).toBe('IC-002')

    // 验证：给定有序 ID 数组，通过 map 查找后顺序不变
    const orderedIds = ['case-2', 'case-1']
    const displayValues = orderedIds.map(id => maps.quotedCases.get(id))
    expect(displayValues).toEqual(['IC-002', 'IC-001'])
  })
})

describe('PageConfig Store — 批量关联加载', () => {
  let store: ReturnType<typeof usePageConfigStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = usePageConfigStore()
  })

  it('fetchPageData 使用批量关联接口加载关联数据', async () => {
    const mockedGet = vi.mocked(get)
    const mockedGetCollectionRelations = vi.mocked(getCollectionRelations)
    mockedGet.mockReset()
    mockedGetCollectionRelations.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
            makeField({
              id: 'f2',
              fieldName: 'relItems',
              controlType: 'relation',
              relationConfig: { targetCollection: 'items', displayField: 'itemName', targetField: 'backRef' },
            }),
          ],
        }),
      ],
    })

    // 1. get('/test') — main data
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'r1', name: 'Record 1' },
        { id: 'r2', name: 'Record 2' },
      ],
      total: 2,
    })
    // 2. getCollectionRelations('test') — batch relations
    mockedGetCollectionRelations.mockResolvedValueOnce({
      'r1': { relItems: ['item-1', 'item-2'] },
      'r2': { relItems: ['item-3'] },
    })
    // 3. get('/items') — resolve relation labels
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'item-1', itemName: '物品A' },
        { id: 'item-2', itemName: '物品B' },
        { id: 'item-3', itemName: '物品C' },
      ],
      total: 3,
    })

    const result = await store.fetchPageData('page-test')

    // 验证使用了批量接口而非逐条请求
    expect(mockedGetCollectionRelations).toHaveBeenCalledWith('test')
    expect(mockedGetCollectionRelations).toHaveBeenCalledTimes(1)

    // 验证关联数据正确分配
    expect(result.data[0].relItems).toEqual(['item-1', 'item-2'])
    expect(result.data[1].relItems).toEqual(['item-3'])

    // 验证标签解析正确
    expect(result.data[0]._rel_relItems_labels).toHaveLength(2)
    expect(result.data[0]._rel_relItems_labels[0].label).toBe('物品A')
  })

  it('resolveRelationLabels 使用共享缓存避免重复请求', async () => {
    const mockedGet = vi.mocked(get)
    const mockedGetCollectionRelations = vi.mocked(getCollectionRelations)
    mockedGet.mockReset()
    mockedGetCollectionRelations.mockReset()

    // 两个字段都指向同一集合 'items'
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
            makeField({
              id: 'f2',
              fieldName: 'relA',
              controlType: 'relation',
              relationConfig: { targetCollection: 'items', displayField: 'itemName', targetField: 'backA' },
            }),
            makeField({
              id: 'f3',
              fieldName: 'refB',
              controlType: 'reference',
              referenceConfig: { targetCollection: 'items', displayField: 'itemName', inheritFields: [] },
            }),
          ],
        }),
      ],
    })

    // 1. get('/test') — main data
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'r1', name: 'Record 1', refB: 'item-1' },
      ],
      total: 1,
    })
    // 2. batch relations
    mockedGetCollectionRelations.mockResolvedValueOnce({
      'r1': { relA: ['item-2'] },
    })
    // 3. get('/items') — shared between resolveRelationLabels and resolveReferences
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'item-1', itemName: '物品A' },
        { id: 'item-2', itemName: '物品B' },
      ],
      total: 2,
    })

    await store.fetchPageData('page-test')

    // 'items' 集合只请求一次（共享缓存）
    const itemsCalls = mockedGet.mock.calls.filter(c => c[0] === '/items')
    expect(itemsCalls).toHaveLength(1)
  })
})

describe('PageConfig Store — resolveRelationImportValues displayField', () => {
  let store: ReturnType<typeof usePageConfigStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = usePageConfigStore()
  })

  it('通过 displayField 名称匹配关联记录', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'relItems',
              controlType: 'relation',
              relationConfig: { targetCollection: 'items', displayField: 'itemName', targetField: 'backRef' },
            }),
          ],
        }),
        // items 集合没有主键
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'item-1', itemName: '物品A' },
        { id: 'item-2', itemName: '物品B' },
      ],
      total: 2,
    })

    // 用户在 Excel 中填写的是 displayField 值
    const records = [
      { relItems: ['物品A', '物品B'] },
    ]

    await store.resolveRelationImportValues('page-test', records)
    expect(records[0].relItems).toEqual(['item-1', 'item-2'])
  })

  it('主键匹配优先于 displayField', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'relItems',
              controlType: 'relation',
              relationConfig: { targetCollection: 'items', displayField: 'itemName', targetField: 'backRef' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-items',
          fields: [
            makeField({ id: 'pk', fieldName: 'code', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'item-1', code: 'X', itemName: '物品1' },
        { id: 'item-2', code: 'Y', itemName: 'X' },
      ],
      total: 2,
    })

    const records = [{ relItems: ['X'] }]
    await store.resolveRelationImportValues('page-test', records)
    // 主键优先：'X' 匹配 code='X' → item-1
    expect(records[0].relItems).toEqual(['item-1'])
  })
})

describe('PageConfig Store — resolveReferenceImportValues', () => {
  let store: ReturnType<typeof usePageConfigStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = usePageConfigStore()
  })

  it('将 displayField 显示值解析为内部记录 ID', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'templateRef',
              controlType: 'reference',
              referenceConfig: { targetCollection: 'templates', displayField: 'templateName', inheritFields: [] },
            }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'tpl-001', templateName: '模板A' },
        { id: 'tpl-002', templateName: '模板B' },
      ],
      total: 2,
    })

    const records = [
      { templateRef: '模板A' },
      { templateRef: '模板B' },
    ]

    await store.resolveReferenceImportValues('page-test', records)
    expect(records[0].templateRef).toBe('tpl-001')
    expect(records[1].templateRef).toBe('tpl-002')
  })

  it('将主键值解析为内部记录 ID', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'templateRef',
              controlType: 'reference',
              referenceConfig: { targetCollection: 'templates', displayField: 'templateName', inheritFields: [] },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-templates',
          fields: [
            makeField({ id: 'pk', fieldName: 'tplCode', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'tpl-001', tplCode: 'TPL-A', templateName: '模板A' },
        { id: 'tpl-002', tplCode: 'TPL-B', templateName: '模板B' },
      ],
      total: 2,
    })

    const records = [
      { templateRef: 'TPL-A' },
      { templateRef: 'TPL-B' },
    ]

    await store.resolveReferenceImportValues('page-test', records)
    expect(records[0].templateRef).toBe('tpl-001')
    expect(records[1].templateRef).toBe('tpl-002')
  })

  it('已经是内部 ID 时保持不变', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'templateRef',
              controlType: 'reference',
              referenceConfig: { targetCollection: 'templates', displayField: 'templateName', inheritFields: [] },
            }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'tpl-001', templateName: '模板A' },
      ],
      total: 1,
    })

    const records = [
      { templateRef: 'tpl-001' },
    ]

    await store.resolveReferenceImportValues('page-test', records)
    expect(records[0].templateRef).toBe('tpl-001')
  })

  it('主键匹配优先于 displayField 匹配', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'ref',
              controlType: 'reference',
              referenceConfig: { targetCollection: 'items', displayField: 'name', inheritFields: [] },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-items',
          fields: [
            makeField({ id: 'pk', fieldName: 'code', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    // code='X' 的记录和 name='X' 的记录不同
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'item-1', code: 'X', name: '物品1' },
        { id: 'item-2', code: 'Y', name: 'X' },
      ],
      total: 2,
    })

    const records = [{ ref: 'X' }]
    await store.resolveReferenceImportValues('page-test', records)
    // 主键匹配优先：'X' 匹配 code='X' → item-1
    expect(records[0].ref).toBe('item-1')
  })

  it('空值跳过不处理', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'ref',
              controlType: 'reference',
              referenceConfig: { targetCollection: 'items', displayField: 'name', inheritFields: [] },
            }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'item-1', name: '物品A' },
      ],
      total: 1,
    })

    const records = [
      { ref: '' },
      { ref: '物品A' },
    ]

    await store.resolveReferenceImportValues('page-test', records)
    expect(records[0].ref).toBe('')
    expect(records[1].ref).toBe('item-1')
  })

  it('无 reference 字段时直接返回，不发请求', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
          ],
        }),
      ],
    })

    const records = [{ name: 'test' }]
    await store.resolveReferenceImportValues('page-test', records)

    expect(mockedGet).not.toHaveBeenCalled()
  })
})

describe('PageConfig Store — resolveCollectionSelectImportValues', () => {
  let store: ReturnType<typeof usePageConfigStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = usePageConfigStore()
  })

  it('将 collection 类型 select 字段的标签值解析为实际选项值', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'category',
              controlType: 'select',
              optionsSource: {
                type: 'collection',
                collection: 'categories',
                labelField: 'catName',
                valueField: 'id',
              },
            }),
          ],
        }),
      ],
    })

    // 目标集合已有记录
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'cat-001', catName: '分类A' },
        { id: 'cat-002', catName: '分类B' },
      ],
      total: 2,
    })

    const records = [
      { category: '分类A' },
      { category: '分类B' },
    ]

    await store.resolveCollectionSelectImportValues('page-test', records)
    expect(records[0].category).toBe('cat-001')
    expect(records[1].category).toBe('cat-002')
  })

  it('将 collection 类型 multiSelect 字段的标签数组解析为实际值', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'tags',
              controlType: 'multiSelect',
              optionsSource: {
                type: 'collection',
                collection: 'tagList',
                labelField: 'tagName',
                valueField: 'id',
              },
            }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'tag-1', tagName: '标签X' },
        { id: 'tag-2', tagName: '标签Y' },
        { id: 'tag-3', tagName: '标签Z' },
      ],
      total: 3,
    })

    const records = [
      { tags: ['标签X', '标签Z'] },
    ]

    await store.resolveCollectionSelectImportValues('page-test', records)
    expect(records[0].tags).toEqual(['tag-1', 'tag-3'])
  })

  it('自引用场景：valueField 为 id 时预生成 ID 并解析', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f-a', fieldName: 'colA', controlType: 'text', order: 0 }),
            makeField({
              id: 'f-c',
              fieldName: 'colC',
              controlType: 'select',
              order: 2,
              optionsSource: {
                type: 'collection',
                collection: 'test',       // 自引用：与 page-test 的 endpoint 相同
                labelField: 'colA',
                valueField: 'id',
              },
            }),
          ],
        }),
      ],
    })

    // 当前集合为空（所有数据都是新导入的）
    mockedGet.mockResolvedValueOnce({ data: [], total: 0 })

    const records: Record<string, any>[] = [
      { colA: '1', colB: '2', colC: '' },    // Row 1
      { colA: '2', colB: '4', colC: '1' },   // Row 2: C 引用 Row 1 的 A
    ]

    await store.resolveCollectionSelectImportValues('page-test', records)

    // Row 1 应有预生成的 _importId
    expect(records[0]._importId).toBeDefined()
    expect(typeof records[0]._importId).toBe('string')
    // Row 2 的 colC 应被解析为 Row 1 的 _importId
    expect(records[1].colC).toBe(records[0]._importId)
  })

  it('自引用场景：valueField 为普通字段时直接映射', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f-a', fieldName: 'colA', controlType: 'text', order: 0 }),
            makeField({
              id: 'f-c',
              fieldName: 'colC',
              controlType: 'select',
              order: 2,
              optionsSource: {
                type: 'collection',
                collection: 'test',
                labelField: 'colA',
                valueField: 'colA',   // labelField === valueField
              },
            }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({ data: [], total: 0 })

    const records: Record<string, any>[] = [
      { colA: '1', colB: '2', colC: '' },
      { colA: '2', colB: '4', colC: '1' },
    ]

    await store.resolveCollectionSelectImportValues('page-test', records)

    // labelField === valueField 时，值不变
    expect(records[1].colC).toBe('1')
    // 不应生成 _importId（valueField 不是 'id'）
    expect(records[0]._importId).toBeUndefined()
  })

  it('跳过空值和非 collection 类型字段', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1',
              fieldName: 'staticSelect',
              controlType: 'select',
              optionsSource: { type: 'static' },
              options: [{ label: 'A', value: 'a' }],
            }),
            makeField({
              id: 'f2',
              fieldName: 'dynSelect',
              controlType: 'select',
              optionsSource: {
                type: 'collection',
                collection: 'items',
                labelField: 'name',
                valueField: 'id',
              },
            }),
          ],
        }),
      ],
    })

    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'item-1', name: '物品A' },
      ],
      total: 1,
    })

    const records = [
      { staticSelect: 'a', dynSelect: '' },    // dynSelect 为空
      { staticSelect: 'a', dynSelect: '物品A' },
    ]

    await store.resolveCollectionSelectImportValues('page-test', records)

    // 静态 select 不受影响
    expect(records[0].staticSelect).toBe('a')
    // 空值跳过
    expect(records[0].dynSelect).toBe('')
    // collection 类型正确解析
    expect(records[1].dynSelect).toBe('item-1')
  })

  it('无 collection 类型字段时直接返回，不发请求', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
            makeField({
              id: 'f2',
              fieldName: 'status',
              controlType: 'select',
              optionsSource: { type: 'static' },
            }),
          ],
        }),
      ],
    })

    const records = [{ name: 'test', status: 'active' }]
    await store.resolveCollectionSelectImportValues('page-test', records)

    expect(mockedGet).not.toHaveBeenCalled()
    expect(records[0].status).toBe('active')
  })

  it('自引用场景：现有记录和导入记录共同构建映射', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f-a', fieldName: 'colA', controlType: 'text', order: 0 }),
            makeField({
              id: 'f-c',
              fieldName: 'colC',
              controlType: 'select',
              order: 2,
              optionsSource: {
                type: 'collection',
                collection: 'test',
                labelField: 'colA',
                valueField: 'id',
              },
            }),
          ],
        }),
      ],
    })

    // 数据库中已有一条记录 colA='existing'
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'test-existing', colA: 'existing' },
      ],
      total: 1,
    })

    const records: Record<string, any>[] = [
      { colA: 'new1', colC: 'existing' },   // 引用已有记录
      { colA: 'new2', colC: 'new1' },        // 引用同批新记录
    ]

    await store.resolveCollectionSelectImportValues('page-test', records)

    // 引用已有记录 → 解析为已有记录的 id
    expect(records[0].colC).toBe('test-existing')
    // 引用同批新记录 → 解析为预生成的 _importId
    expect(records[1].colC).toBe(records[0]._importId)
  })
})

describe('PageConfig Store — batchDeletePageData', () => {
  let store: ReturnType<typeof usePageConfigStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = usePageConfigStore()
  })

  it('批量删除成功，更新缓存', async () => {
    const mockedPost = vi.mocked(post)
    mockedPost.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [makeField({ id: 'f1', fieldName: 'name', controlType: 'text' })],
        }),
      ],
    })
    store.pageDataCache = {
      'page-test': [
        { id: 'r1', name: 'Record 1' },
        { id: 'r2', name: 'Record 2' },
        { id: 'r3', name: 'Record 3' },
      ],
    }

    mockedPost.mockResolvedValueOnce({ deleted: 2, blocked: {} })

    const result = await store.batchDeletePageData('page-test', ['r1', 'r3'])

    expect(mockedPost).toHaveBeenCalledWith('/test/batch-delete', { ids: ['r1', 'r3'] })
    expect(result.deleted).toBe(2)
    expect(store.pageDataCache['page-test']).toHaveLength(1)
    expect(store.pageDataCache['page-test'][0].id).toBe('r2')
  })

  it('批量删除部分被阻止，只移除未阻止的记录', async () => {
    const mockedPost = vi.mocked(post)
    mockedPost.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [makeField({ id: 'f1', fieldName: 'name', controlType: 'text' })],
        }),
      ],
    })
    store.pageDataCache = {
      'page-test': [
        { id: 'r1', name: 'Record 1' },
        { id: 'r2', name: 'Record 2' },
        { id: 'r3', name: 'Record 3' },
      ],
    }

    // r2 被阻止删除（如有关联数据）
    mockedPost.mockResolvedValueOnce({
      deleted: 2,
      blocked: { 'r2': '存在关联数据' },
    })

    const result = await store.batchDeletePageData('page-test', ['r1', 'r2', 'r3'])

    expect(result.deleted).toBe(2)
    expect(result.blocked).toEqual({ 'r2': '存在关联数据' })
    // 只有 r2 保留
    expect(store.pageDataCache['page-test']).toHaveLength(1)
    expect(store.pageDataCache['page-test'][0].id).toBe('r2')
  })

  it('批量删除失败抛出错误', async () => {
    const mockedPost = vi.mocked(post)
    mockedPost.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [makeField({ id: 'f1', fieldName: 'name', controlType: 'text' })],
        }),
      ],
    })
    store.pageDataCache = {
      'page-test': [{ id: 'r1', name: 'Record 1' }],
    }

    mockedPost.mockRejectedValueOnce(new Error('删除失败'))

    await expect(store.batchDeletePageData('page-test', ['r1'])).rejects.toThrow('删除失败')
  })

  it('无缓存时不报错', async () => {
    const mockedPost = vi.mocked(post)
    mockedPost.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [makeField({ id: 'f1', fieldName: 'name', controlType: 'text' })],
        }),
      ],
    })
    store.pageDataCache = {}

    mockedPost.mockResolvedValueOnce({ deleted: 1, blocked: {} })

    const result = await store.batchDeletePageData('page-test', ['r1'])
    expect(result.deleted).toBe(1)
  })
})

describe('PageConfig Store — refreshSingleRecord', () => {
  let store: ReturnType<typeof usePageConfigStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = usePageConfigStore()
  })

  it('刷新存在的记录，更新缓存', async () => {
    const mockedGet = vi.mocked(get)
    const mockedGetRecordRelations = vi.mocked(getRecordRelations)
    mockedGet.mockReset()
    mockedGetRecordRelations.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
            makeField({
              id: 'f2',
              fieldName: 'relItems',
              controlType: 'relation',
              relationConfig: { targetCollection: 'items', displayField: 'itemName', targetField: 'backRef' },
            }),
          ],
        }),
      ],
    })
    store.pageDataCache = {
      'page-test': [
        { id: 'r1', name: 'Old Name', relItems: [] },
      ],
    }

    // 获取单条记录
    mockedGet.mockResolvedValueOnce({ id: 'r1', name: 'New Name' })
    // 获取记录关联
    mockedGetRecordRelations.mockResolvedValueOnce({ relItems: ['item-1'] })
    // 获取关联标签
    mockedGet.mockResolvedValueOnce({
      data: [{ id: 'item-1', itemName: '物品A' }],
      total: 1,
    })

    const result = await store.refreshSingleRecord('page-test', 'r1')

    expect(mockedGet).toHaveBeenCalledWith('/test/r1')
    expect(mockedGetRecordRelations).toHaveBeenCalledWith('test', 'r1')
    expect(result).not.toBeNull()
    expect(result!.name).toBe('New Name')
    expect(result!.relItems).toEqual(['item-1'])
    expect(result!._rel_relItems_labels).toHaveLength(1)

    // 验证缓存更新
    expect(store.pageDataCache['page-test'][0].name).toBe('New Name')
  })

  it('刷新新记录，追加到缓存末尾', async () => {
    const mockedGet = vi.mocked(get)
    const mockedGetRecordRelations = vi.mocked(getRecordRelations)
    mockedGet.mockReset()
    mockedGetRecordRelations.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [makeField({ id: 'f1', fieldName: 'name', controlType: 'text' })],
        }),
      ],
    })
    store.pageDataCache = {
      'page-test': [
        { id: 'r1', name: 'Record 1' },
      ],
    }

    mockedGet.mockResolvedValueOnce({ id: 'r2', name: 'New Record' })
    mockedGetRecordRelations.mockResolvedValueOnce({})

    const result = await store.refreshSingleRecord('page-test', 'r2')

    expect(result).not.toBeNull()
    expect(result!.id).toBe('r2')
    expect(store.pageDataCache['page-test']).toHaveLength(2)
    expect(store.pageDataCache['page-test'][1].id).toBe('r2')
  })

  it('获取失败返回 null', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [makeField({ id: 'f1', fieldName: 'name', controlType: 'text' })],
        }),
      ],
    })
    store.pageDataCache = {
      'page-test': [{ id: 'r1', name: 'Record 1' }],
    }

    mockedGet.mockRejectedValueOnce(new Error('记录不存在'))

    const result = await store.refreshSingleRecord('page-test', 'r1')

    expect(result).toBeNull()
  })

  it('关联数据获取失败时不影响主记录', async () => {
    const mockedGet = vi.mocked(get)
    const mockedGetRecordRelations = vi.mocked(getRecordRelations)
    mockedGet.mockReset()
    mockedGetRecordRelations.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
            makeField({
              id: 'f2',
              fieldName: 'relItems',
              controlType: 'relation',
              relationConfig: { targetCollection: 'items', displayField: 'itemName', targetField: 'backRef' },
            }),
          ],
        }),
      ],
    })
    store.pageDataCache = {}

    mockedGet.mockResolvedValueOnce({ id: 'r1', name: 'Test Record' })
    mockedGetRecordRelations.mockRejectedValueOnce(new Error('关联获取失败'))
    mockedGet.mockResolvedValueOnce({ data: [], total: 0 })

    const result = await store.refreshSingleRecord('page-test', 'r1')

    expect(result).not.toBeNull()
    expect(result!.name).toBe('Test Record')
    // 关联字段应为空数组
    expect(result!.relItems).toEqual([])
  })

  it('解析 reference 字段', async () => {
    const mockedGet = vi.mocked(get)
    const mockedGetRecordRelations = vi.mocked(getRecordRelations)
    mockedGet.mockReset()
    mockedGetRecordRelations.mockReset()

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'name', controlType: 'text' }),
            makeField({
              id: 'f2',
              fieldName: 'templateRef',
              controlType: 'reference',
              referenceConfig: { targetCollection: 'templates', displayField: 'tplName', inheritFields: ['desc'] },
            }),
          ],
        }),
      ],
    })
    store.pageDataCache = {}

    mockedGet.mockResolvedValueOnce({ id: 'r1', name: 'Test', templateRef: 'tpl-1' })
    mockedGetRecordRelations.mockResolvedValueOnce({})
    // 获取引用记录
    mockedGet.mockResolvedValueOnce({
      data: [
        { id: 'tpl-1', tplName: '模板A', desc: '描述A' },
      ],
      total: 1,
    })

    const result = await store.refreshSingleRecord('page-test', 'r1')

    expect(result).not.toBeNull()
    expect(result!.templateRef).toBe('tpl-1')
    expect(result!._ref_templateRef_display).toBe('模板A')
    expect(result!._ref_templateRef_desc).toBe('描述A')
  })
})
