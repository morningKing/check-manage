import { describe, it, expect, beforeAll, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import QueryResultBlock from '../QueryResultBlock.vue'

const writeFile = vi.fn()
vi.mock('xlsx', () => ({
  utils: { json_to_sheet: vi.fn(() => ({})), book_new: vi.fn(() => ({})), book_append_sheet: vi.fn() },
  writeFile: (...a: any[]) => writeFile(...a),
}))

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any
})

const stubs = {
  'el-table': { template: '<table><slot /></table>' },
  'el-table-column': { template: '<col-stub />' },
  'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>', emits: ['click'] },
  'el-icon': { template: '<i><slot /></i>' },
}

describe('QueryResultBlock', () => {
  it('table mode renders a table and downloads via SheetJS', async () => {
    const result = { mode: 'table', collection: 'orders', total: 1,
      columns: [{ key: 'no', label: '单号' }], rows: [{ no: 'A1' }] }
    const w = mount(QueryResultBlock, { props: { result, downloadUrl: (p: string) => '/dl/' + p }, global: { stubs } })
    expect(w.text()).toContain('共 1 条')
    expect(w.find('table').exists()).toBe(true)
    await w.find('button').trigger('click')
    expect(writeFile).toHaveBeenCalled()
  })

  it('file mode renders a download link and no table', () => {
    const result = { mode: 'file', collection: 'orders', total: 999, file: 'outputs/x.xlsx' }
    const w = mount(QueryResultBlock, { props: { result, downloadUrl: (p: string) => '/dl/' + p }, global: { stubs } })
    expect(w.find('table').exists()).toBe(false)
    expect(w.find('a').attributes('href')).toBe('/dl/outputs/x.xlsx')
    expect(w.text()).toContain('999')
  })
})
