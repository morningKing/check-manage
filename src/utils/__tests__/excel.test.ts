import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  getExportableFields,
  exportToExcel,
  generateImportTemplate,
} from '../excel'
import {
  parseWorkbookBuffer,
  parseJsonText,
  readWorkbookMeta,
  processWorkbookRowRange,
  readJsonMeta,
  processJsonItemRange,
} from '../excelParseCore'
import type { FieldConfig, ControlType } from '@/types'

// Mock xlsx library
vi.mock('xlsx', () => ({
  utils: {
    aoa_to_sheet: vi.fn(() => ({ '!cols': [] })),
    sheet_to_json: vi.fn(),
    book_new: vi.fn(() => ({})),
    book_append_sheet: vi.fn(),
  },
  read: vi.fn(),
  writeFile: vi.fn(),
}))

import * as XLSX from 'xlsx'

function makeField(overrides: Partial<FieldConfig>): FieldConfig {
  return {
    fieldName: 'field1',
    label: '字段1',
    controlType: 'text',
    order: 0,
    hidden: false,
    required: false,
    ...overrides,
  } as FieldConfig
}

describe('Excel Utils', () => {
  describe('getExportableFields', () => {
    it('过滤可导出类型', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', controlType: 'text', order: 1 }),
        makeField({ fieldName: 'avatar', controlType: 'image', order: 2 }),
        makeField({ fieldName: 'count', controlType: 'number', order: 3 }),
        makeField({ fieldName: 'file', controlType: 'file', order: 4 }),
      ]

      const result = getExportableFields(fields)
      expect(result).toHaveLength(2)
      expect(result[0].fieldName).toBe('name')
      expect(result[1].fieldName).toBe('count')
    })

    it('排除 hidden 字段', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'visible', controlType: 'text', order: 1, hidden: false }),
        makeField({ fieldName: 'hidden', controlType: 'text', order: 2, hidden: true }),
      ]

      const result = getExportableFields(fields)
      expect(result).toHaveLength(1)
      expect(result[0].fieldName).toBe('visible')
    })

    it('按 order 排序', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'c', controlType: 'text', order: 3 }),
        makeField({ fieldName: 'a', controlType: 'text', order: 1 }),
        makeField({ fieldName: 'b', controlType: 'number', order: 2 }),
      ]

      const result = getExportableFields(fields)
      expect(result.map((f) => f.fieldName)).toEqual(['a', 'b', 'c'])
    })

    it('支持所有可导出类型', () => {
      const exportableTypes = ['text', 'textarea', 'number', 'date', 'datetime', 'select', 'multiSelect', 'radio', 'checkbox', 'relation', 'reference', 'autoTimestamp', 'autoSequence', 'quoteSelect']
      const fields = exportableTypes.map((type, i) =>
        makeField({ fieldName: `f${i}`, controlType: type as ControlType, order: i })
      )

      const result = getExportableFields(fields)
      expect(result).toHaveLength(exportableTypes.length)
    })

    it('autoSequence 字段可导出', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'seqNo', controlType: 'autoSequence', order: 1 }),
        makeField({ fieldName: 'name', controlType: 'text', order: 2 }),
      ]
      const result = getExportableFields(fields)
      expect(result).toHaveLength(2)
      expect(result[0].fieldName).toBe('seqNo')
    })

    it('autoTimestamp 字段可导出', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'ts', controlType: 'autoTimestamp', order: 1 }),
      ]
      const result = getExportableFields(fields)
      expect(result).toHaveLength(1)
      expect(result[0].controlType).toBe('autoTimestamp')
    })

    it('空数组返回空数组', () => {
      expect(getExportableFields([])).toEqual([])
    })

    it('quoteSelect 字段可导出', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'quoted', controlType: 'quoteSelect', order: 1 }),
        makeField({ fieldName: 'name', controlType: 'text', order: 2 }),
      ]
      const result = getExportableFields(fields)
      expect(result).toHaveLength(2)
      expect(result[0].fieldName).toBe('quoted')
    })
  })

  describe('exportToExcel', () => {
    const mockWriteFile = vi.mocked(XLSX.writeFile)
    const mockAoatoSheet = vi.mocked(XLSX.utils.aoa_to_sheet)
    const mockBookNew = vi.mocked(XLSX.utils.book_new)
    const mockBookAppendSheet = vi.mocked(XLSX.utils.book_append_sheet)

    beforeEach(() => {
      vi.clearAllMocks()
    })

    it('基本导出功能', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
        makeField({ fieldName: 'age', label: '年龄', controlType: 'number', order: 2 }),
      ]
      const data = [
        { name: '张三', age: 25 },
        { name: '李四', age: 30 },
      ]

      exportToExcel(data, fields, 'test-export')

      expect(mockAoatoSheet).toHaveBeenCalled()
      expect(mockBookNew).toHaveBeenCalled()
      expect(mockBookAppendSheet).toHaveBeenCalled()
      expect(mockWriteFile).toHaveBeenCalledWith(expect.anything(), 'test-export.xlsx')
    })

    it('空数据导出只有表头', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      exportToExcel([], fields, 'empty-export')

      expect(mockAoatoSheet).toHaveBeenCalled()
    })

    it('自定义文件名', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]
      const data = [{ name: 'test' }]

      exportToExcel(data, fields, 'custom-file-name')

      expect(mockWriteFile).toHaveBeenCalledWith(expect.anything(), 'custom-file-name.xlsx')
    })

    it('select 字段值转标签', () => {
      const fields: FieldConfig[] = [
        makeField({
          fieldName: 'status',
          label: '状态',
          controlType: 'select',
          order: 1,
          options: [
            { label: '启用', value: 'active' },
            { label: '禁用', value: 'inactive' },
          ],
        }),
      ]
      const data = [{ status: 'active' }]

      exportToExcel(data, fields, 'test')

      const callArgs = mockAoatoSheet.mock.calls[0][0]
      const dataRow = callArgs[1]
      expect(dataRow[0]).toBe('启用')
    })

    it('multiSelect 字段值转标签并用顿号分隔', () => {
      const fields: FieldConfig[] = [
        makeField({
          fieldName: 'tags',
          label: '标签',
          controlType: 'multiSelect',
          order: 1,
          options: [
            { label: '标签A', value: 'a' },
            { label: '标签B', value: 'b' },
          ],
        }),
      ]
      const data = [{ tags: ['a', 'b'] }]

      exportToExcel(data, fields, 'test')

      const callArgs = mockAoatoSheet.mock.calls[0][0]
      const dataRow = callArgs[1]
      expect(dataRow[0]).toBe('标签A、标签B')
    })
  })

  describe('generateImportTemplate', () => {
    const mockWriteFile = vi.mocked(XLSX.writeFile)

    beforeEach(() => {
      vi.clearAllMocks()
    })

    it('生成导入模板', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1, required: true }),
        makeField({ fieldName: 'age', label: '年龄', controlType: 'number', order: 2, required: false }),
      ]

      generateImportTemplate(fields, 'import-template')

      expect(mockWriteFile).toHaveBeenCalledWith(expect.anything(), 'import-template.xlsx')
    })

    it('模板包含字段说明工作表', () => {
      const fields: FieldConfig[] = [
        makeField({
          fieldName: 'status',
          label: '状态',
          controlType: 'select',
          order: 1,
          options: [
            { label: '启用', value: 'active' },
            { label: '禁用', value: 'inactive' },
          ],
        }),
      ]

      generateImportTemplate(fields, 'test')

      // 应该调用两次 book_append_sheet：一次是数据模板，一次是字段说明
      const mockBookAppendSheet = vi.mocked(XLSX.utils.book_append_sheet)
      expect(mockBookAppendSheet).toHaveBeenCalledTimes(2)
    })
  })

  // parseImportFile/parseJsonImportFile（src/utils/excel.ts）现在只是
  // 转发给 Web Worker 的胶水代码（见 workers/excelImportWorker.ts，避免
  // 大文件解析冻结页面）——Worker 在 jsdom 下跑不起来，实际解析逻辑测的是
  // excelParseCore.ts 里的同步纯函数 parseWorkbookBuffer/parseJsonText。
  describe('parseWorkbookBuffer', () => {
    const mockRead = vi.mocked(XLSX.read)
    const mockSheetToJson = vi.mocked(XLSX.utils.sheet_to_json)

    beforeEach(() => {
      vi.clearAllMocks()
    })

    it('解析正常 Excel 文件', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
        makeField({ fieldName: 'age', label: '年龄', controlType: 'number', order: 2 }),
      ]

      const mockWorkbook = {
        SheetNames: ['Sheet1'],
        Sheets: {
          Sheet1: {},
        },
      }
      mockRead.mockReturnValue(mockWorkbook as any)
      mockSheetToJson.mockReturnValue([
        ['名称', '年龄'],
        ['张三', 25],
        ['李四', 30],
      ])

      const result = parseWorkbookBuffer(new ArrayBuffer(0), fields)

      expect(result).toHaveLength(2)
      expect(result[0].name).toBe('张三')
      // labelToValue returns string for numbers
      expect(result[0].age).toBe('25')
      expect(result[1].name).toBe('李四')
      expect(result[1].age).toBe('30')
    })

    it('空文件返回空数组', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      const mockWorkbook = {
        SheetNames: ['Sheet1'],
        Sheets: {
          Sheet1: {},
        },
      }
      mockRead.mockReturnValue(mockWorkbook as any)
      mockSheetToJson.mockReturnValue([['名称']])

      const result = parseWorkbookBuffer(new ArrayBuffer(0), fields)

      expect(result).toEqual([])
    })

    it('支持字段名作为表头', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      const mockWorkbook = {
        SheetNames: ['Sheet1'],
        Sheets: {
          Sheet1: {},
        },
      }
      mockRead.mockReturnValue(mockWorkbook as any)
      mockSheetToJson.mockReturnValue([
        ['name'],
        ['张三'],
      ])

      const result = parseWorkbookBuffer(new ArrayBuffer(0), fields)

      expect(result).toHaveLength(1)
      expect(result[0].name).toBe('张三')
    })

    it('跳过空行', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      const mockWorkbook = {
        SheetNames: ['Sheet1'],
        Sheets: {
          Sheet1: {},
        },
      }
      mockRead.mockReturnValue(mockWorkbook as any)
      mockSheetToJson.mockReturnValue([
        ['名称'],
        ['张三'],
        [null],
        ['李四'],
      ])

      const result = parseWorkbookBuffer(new ArrayBuffer(0), fields)

      expect(result).toHaveLength(2)
      expect(result[0].name).toBe('张三')
      expect(result[1].name).toBe('李四')
    })

    it('select 字段标签转值', () => {
      const fields: FieldConfig[] = [
        makeField({
          fieldName: 'status',
          label: '状态',
          controlType: 'select',
          order: 1,
          options: [
            { label: '启用', value: 'active' },
            { label: '禁用', value: 'inactive' },
          ],
        }),
      ]

      const mockWorkbook = {
        SheetNames: ['Sheet1'],
        Sheets: {
          Sheet1: {},
        },
      }
      mockRead.mockReturnValue(mockWorkbook as any)
      mockSheetToJson.mockReturnValue([
        ['状态'],
        ['启用'],
        ['禁用'],
      ])

      const result = parseWorkbookBuffer(new ArrayBuffer(0), fields)

      expect(result).toHaveLength(2)
      expect(result[0].status).toBe('active')
      expect(result[1].status).toBe('inactive')
    })

    it('multiSelect 字段标签转值', () => {
      const fields: FieldConfig[] = [
        makeField({
          fieldName: 'tags',
          label: '标签',
          controlType: 'multiSelect',
          order: 1,
          options: [
            { label: '标签A', value: 'a' },
            { label: '标签B', value: 'b' },
          ],
        }),
      ]

      const mockWorkbook = {
        SheetNames: ['Sheet1'],
        Sheets: {
          Sheet1: {},
        },
      }
      mockRead.mockReturnValue(mockWorkbook as any)
      mockSheetToJson.mockReturnValue([
        ['标签'],
        ['标签A、标签B'],
      ])

      const result = parseWorkbookBuffer(new ArrayBuffer(0), fields)

      expect(result).toHaveLength(1)
      expect(result[0].tags).toEqual(['a', 'b'])
    })

    it('XLSX.read 抛出的错误会同步传播（worker 里会被捕获转成 reject）', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      mockRead.mockImplementation(() => {
        throw new Error('文件已损坏')
      })

      expect(() => parseWorkbookBuffer(new ArrayBuffer(0), fields)).toThrow('文件已损坏')
    })
  })

  // readWorkbookMeta/processWorkbookRowRange、readJsonMeta/processJsonItemRange
  // 是 excelImportWorker.ts 用来分片解析 + 分片回报进度的底层原语（大文件时
  // 避免一次性 postMessage 整个 records 数组造成的一次性主线程卡顿）。
  // parseWorkbookBuffer/parseJsonText 就是"meta + 整段 range"的薄封装，
  // 这里单独测分片本身：任意切法拼起来必须等价于整段一次处理的结果。
  describe('readWorkbookMeta + processWorkbookRowRange（分片解析）', () => {
    const mockRead = vi.mocked(XLSX.read)
    const mockSheetToJson = vi.mocked(XLSX.utils.sheet_to_json)

    beforeEach(() => {
      vi.clearAllMocks()
    })

    it('分两片处理，结果与整段一次处理一致', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]
      const mockWorkbook = { SheetNames: ['Sheet1'], Sheets: { Sheet1: {} } }
      mockRead.mockReturnValue(mockWorkbook as any)
      mockSheetToJson.mockReturnValue([
        ['名称'],
        ['张三'],
        ['李四'],
        ['王五'],
      ])

      const meta = readWorkbookMeta(new ArrayBuffer(0), fields)
      expect(meta.dataRows).toHaveLength(3)

      const chunk1 = processWorkbookRowRange(meta, 0, 2)
      const chunk2 = processWorkbookRowRange(meta, 2, 3)

      expect(chunk1.map((r) => r.name)).toEqual(['张三', '李四'])
      expect(chunk2.map((r) => r.name)).toEqual(['王五'])
      expect([...chunk1, ...chunk2]).toEqual(parseWorkbookBuffer(new ArrayBuffer(0), fields))
    })

    it('end 超过数据行数时不越界', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]
      const mockWorkbook = { SheetNames: ['Sheet1'], Sheets: { Sheet1: {} } }
      mockRead.mockReturnValue(mockWorkbook as any)
      mockSheetToJson.mockReturnValue([['名称'], ['张三']])

      const meta = readWorkbookMeta(new ArrayBuffer(0), fields)
      const result = processWorkbookRowRange(meta, 0, 999)

      expect(result).toHaveLength(1)
    })

    it('sheet 因体积过大被 SheetJS 静默剔除（SheetNames 有名字但 Sheets 里没有）时抛出明确错误，而不是当成空文件', () => {
      // 复现真实场景：某个 sheet 解压后的 XML 超过 Node/V8 单个字符串的长度
      // 上限（0x1fffffe8 ≈ 512MB，行多、内联字符串多的大表格容易撞到）时，
      // SheetJS 内部按 sheet 单独 try/catch（源码里叫 safe_parse_sheet），
      // 不会让 XLSX.read() 整体抛错，而是把这个 sheet 从 wb.Sheets 里悄悄
      // 剔除——SheetNames 里还留着名字，wb.Sheets[name] 却是 undefined。
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]
      const mockWorkbook = { SheetNames: ['数据'], Sheets: {} } // Sheets 里没有 '数据'
      mockRead.mockReturnValue(mockWorkbook as any)

      expect(() => readWorkbookMeta(new ArrayBuffer(0), fields)).toThrow('解析失败')
      // 不应该走到 sheet_to_json 那一步（undefined 传进去没有意义）
      expect(mockSheetToJson).not.toHaveBeenCalled()
    })
  })

  describe('readJsonMeta + processJsonItemRange（分片解析）', () => {
    it('分两片处理，结果与整段一次处理一致', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]
      const text = JSON.stringify([{ 名称: '张三' }, { 名称: '李四' }, { 名称: '王五' }])

      const meta = readJsonMeta(text, fields)
      expect(meta.items).toHaveLength(3)

      const chunk1 = processJsonItemRange(meta, 0, 2)
      const chunk2 = processJsonItemRange(meta, 2, 3)

      expect(chunk1.map((r) => r.name)).toEqual(['张三', '李四'])
      expect(chunk2.map((r) => r.name)).toEqual(['王五'])
      expect([...chunk1, ...chunk2]).toEqual(parseJsonText(text, fields))
    })

    it('非数组 JSON 在 readJsonMeta 阶段就抛出错误', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]
      expect(() => readJsonMeta(JSON.stringify({ name: '张三' }), fields)).toThrow('JSON 文件内容必须是数组')
    })
  })

  describe('parseJsonText', () => {
    it('解析正常 JSON 文件（key 为字段标签）', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
        makeField({ fieldName: 'age', label: '年龄', controlType: 'number', order: 2 }),
      ]

      const text = JSON.stringify([
        { '名称': '张三', '年龄': 25 },
        { '名称': '李四', '年龄': 30 },
      ])
      const result = parseJsonText(text, fields)

      expect(result).toHaveLength(2)
      expect(result[0].name).toBe('张三')
      expect(result[0].age).toBe(25)
      expect(result[1].name).toBe('李四')
      expect(result[1].age).toBe(30)
    })

    it('支持 fieldName 作为 key', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      const text = JSON.stringify([{ name: '张三' }])
      const result = parseJsonText(text, fields)

      expect(result).toHaveLength(1)
      expect(result[0].name).toBe('张三')
    })

    it('空数组返回空', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      const result = parseJsonText(JSON.stringify([]), fields)

      expect(result).toEqual([])
    })

    it('非数组 JSON 抛出错误', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      const text = JSON.stringify({ name: '张三' })

      expect(() => parseJsonText(text, fields)).toThrow('JSON 文件内容必须是数组')
    })

    it('语法错误的 JSON 抛出错误', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      expect(() => parseJsonText('{not valid json', fields)).toThrow()
    })

    it('select 字段标签转值', () => {
      const fields: FieldConfig[] = [
        makeField({
          fieldName: 'status',
          label: '状态',
          controlType: 'select',
          order: 1,
          options: [
            { label: '启用', value: 'active' },
            { label: '禁用', value: 'inactive' },
          ],
        }),
      ]

      const text = JSON.stringify([{ '状态': '启用' }, { '状态': '禁用' }])
      const result = parseJsonText(text, fields)

      expect(result).toHaveLength(2)
      expect(result[0].status).toBe('active')
      expect(result[1].status).toBe('inactive')
    })

    it('multiSelect 字段 — 数组值中 label 转 value', () => {
      const fields: FieldConfig[] = [
        makeField({
          fieldName: 'tags',
          label: '标签',
          controlType: 'multiSelect',
          order: 1,
          options: [
            { label: '标签A', value: 'a' },
            { label: '标签B', value: 'b' },
          ],
        }),
      ]

      const text = JSON.stringify([{ '标签': ['标签A', '标签B'] }])
      const result = parseJsonText(text, fields)

      expect(result).toHaveLength(1)
      expect(result[0].tags).toEqual(['a', 'b'])
    })

    it('multiSelect 字段 — 字符串值分割转换', () => {
      const fields: FieldConfig[] = [
        makeField({
          fieldName: 'tags',
          label: '标签',
          controlType: 'multiSelect',
          order: 1,
          options: [
            { label: '标签A', value: 'a' },
            { label: '标签B', value: 'b' },
          ],
        }),
      ]

      const text = JSON.stringify([{ '标签': '标签A、标签B' }])
      const result = parseJsonText(text, fields)

      expect(result).toHaveLength(1)
      expect(result[0].tags).toEqual(['a', 'b'])
    })

    it('number 字段保留数字类型', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'count', label: '数量', controlType: 'number', order: 1 }),
      ]

      const text = JSON.stringify([{ '数量': 42 }])
      const result = parseJsonText(text, fields)

      expect(result).toHaveLength(1)
      expect(result[0].count).toBe(42)
      expect(typeof result[0].count).toBe('number')
    })

    it('跳过空值对象', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      const text = JSON.stringify([
        { '名称': '张三' },
        { '名称': '' },
        { '名称': null },
        { '未知字段': '值' },
        { '名称': '李四' },
      ])
      const result = parseJsonText(text, fields)

      expect(result).toHaveLength(2)
      expect(result[0].name).toBe('张三')
      expect(result[1].name).toBe('李四')
    })
  })
})
