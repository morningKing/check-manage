import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import AdminBatchFiles from '../AdminBatchFiles.vue'
import type { AdminChildFile } from '@/api/aiBatchAdmin'

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

const files: AdminChildFile[] = [
  { name: 'a.txt', path: 'outputs/a.txt', dir: 'outputs', size: 1024, dataFileId: null },
  { name: 'b.md', path: 'outputs/b.md', dir: 'outputs', size: 512, dataFileId: 'df-1' },
  { name: 'c.md', path: 'root.md', dir: 'workspace', size: 200, dataFileId: null },
]

function makeWrapper(props: any) {
  return mount(AdminBatchFiles, {
    props: { batchId: 'batch-1', sessionId: 'sess-1', files, truncated: false, loading: false, ...props },
    global: { stubs: {
      'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>', emits: ['click'] },
      'el-checkbox': {
        template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
        props: ['modelValue'],
        emits: ['update:modelValue'],
      },
      'el-empty': { template: '<div>empty</div>' },
      'el-icon': { template: '<span><slot /></span>' },
      'el-scrollbar': { template: '<div><slot /></div>' },
      'el-alert': { template: '<div class="alert">{{ title }}</div>', props: ['title'] },
      ArrowRight: true,
    } },
  })
}

describe('AdminBatchFiles', () => {
  it('groups files by dir', () => {
    const w = makeWrapper({})
    const groupHeaders = w.findAll('.admin-files__group-head')
    expect(groupHeaders.length).toBeGreaterThanOrEqual(2)
  })

  it('已导入 (dataFileId 非空) 的行 checkbox disabled + 标签', () => {
    const w = makeWrapper({})
    const importedRow = w.findAll('.admin-files__row').find(r => r.text().includes('b.md'))
    expect(importedRow?.text()).toContain('已导入')
    expect(importedRow?.find('input[type="checkbox"]').attributes('disabled')).toBeDefined()
  })

  it('选中若干 → emits import with paths', async () => {
    const w = makeWrapper({})
    const unimportedCheckbox = w.findAll('input[type="checkbox"]')
      .find(c => !c.attributes('disabled'))
    await unimportedCheckbox?.setValue(true)
    await w.find('[data-testid="import-btn"]').trigger('click')
    const ev = w.emitted('import')
    expect(ev).toBeTruthy()
    expect(ev![0][0]).toContain('outputs/a.txt')
    expect(ev![0][0]).not.toContain('outputs/b.md')
  })

  it('全部未导入时全选复用', () => {
    const w = makeWrapper({ files: [
      { name: 'a.txt', path: 'outputs/a.txt', dir: 'outputs', size: 1, dataFileId: null }
    ] })
    expect(w.find('[data-testid="select-all"]').isVisible()).toBe(true)
  })

  it('truncated=true 时显示截断提示', () => {
    const w = makeWrapper({ truncated: true })
    expect(w.text()).toContain('仅显示最近')
  })

  it('loading=true 时显示加载中', () => {
    const w = makeWrapper({ loading: true })
    expect(w.text()).toMatch(/加载中|loading/i)
  })
})