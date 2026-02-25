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
  updateFieldRelations: vi.fn(),
}))

vi.mock('uuid', () => ({
  v4: () => 'mock-uuid-1234',
}))

import { usePageConfigStore } from '../pageConfig'
import { get } from '@/utils/request'
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
      store.$patch({
        pageDataCache: {
          'page-demo': [
            { id: 'r1', seqNo: 'IC-001' },
            { id: 'r2', seqNo: 'IC-005' },
            { id: 'r3', seqNo: 'IC-003' },
          ],
        },
      })

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
        pageDataCache: {
          'page-demo': [
            { id: 'r1', seqNo: 'IC-010' },
            { id: 'r2', seqNo: 'RM-03' },
          ],
        },
      })

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
        pageDataCache: {
          'page-demo': [
            { id: 'r1', seqNo: 'T-00042' },
          ],
        },
      })

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
        pageDataCache: {
          'page-demo': [
            { id: 'r1', seqNo: 'IC-abc' },
            { id: 'r2', seqNo: 'IC-002' },
          ],
        },
      })

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
    mockedGet.mockResolvedValueOnce([
      { id: 'r1', quotedCases: ['case-1', 'case-2'] },
      { id: 'r2', quotedCases: [] },
    ])
    mockedGet.mockResolvedValueOnce([
      { id: 'case-1', caseName: '用例A' },
      { id: 'case-2', caseName: '用例B' },
    ])

    const result = await store.fetchPageData('page-test')
    const labels = result[0]?.[`_quote_quotedCases_labels`]
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
    mockedGet.mockResolvedValueOnce([
      { id: 'r1', quotedCases: ['case-3', 'case-1', 'case-2'] },
    ])
    mockedGet.mockResolvedValueOnce([
      { id: 'case-1', caseName: '用例A' },
      { id: 'case-2', caseName: '用例B' },
      { id: 'case-3', caseName: '用例C' },
    ])

    const result = await store.fetchPageData('page-test')
    const labels = result[0]?.[`_quote_quotedCases_labels`]
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
    mockedGet.mockResolvedValueOnce([
      { id: 'r1', quotedCases: ['id-B', 'id-C', 'id-D'] },
    ])
    mockedGet.mockResolvedValueOnce([
      { id: 'id-B', caseName: '记录B' },
      // id-C 已删除，不在目标集合中
      { id: 'id-D', caseName: '记录D' },
    ])

    const result = await store.fetchPageData('page-test')
    const labels = result[0]?.[`_quote_quotedCases_labels`]
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

    mockedGet.mockResolvedValueOnce([
      { id: 'case-1', caseId: 'IC-001' },
      { id: 'case-2', caseId: 'IC-002' },
    ])

    const maps = await store.fetchQuoteDisplayMaps('page-test')
    expect(maps.quotedCases).toBeDefined()
    expect(maps.quotedCases.get('case-1')).toBe('IC-001')
    expect(maps.quotedCases.get('case-2')).toBe('IC-002')
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

    mockedGet.mockResolvedValueOnce([
      { id: 'case-1', caseId: 'IC-001' },
      { id: 'case-2', caseId: 'IC-002' },
    ])

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

    mockedGet.mockResolvedValueOnce([
      { id: 'case-1', caseId: 'IC-001' },
      { id: 'case-2', caseId: 'IC-002' },
      { id: 'case-3', caseId: 'IC-003' },
    ])

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

    mockedGet.mockResolvedValueOnce([
      { id: 'case-1', caseId: 'IC-001' },
      { id: 'case-3', caseId: 'IC-003' },
    ])

    // IC-002 不存在，应被过滤但不影响 IC-001 和 IC-003 的顺序
    const records = [
      { quotedCases: ['IC-001', 'IC-002', 'IC-003'] },
    ]

    await store.resolveQuoteImportValues('page-test', records)
    expect(records[0].quotedCases).toEqual(['case-1', 'case-3'])
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

    mockedGet.mockResolvedValueOnce([
      { id: 'case-1', caseId: 'IC-001' },
      { id: 'case-2', caseId: 'IC-002' },
    ])

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
