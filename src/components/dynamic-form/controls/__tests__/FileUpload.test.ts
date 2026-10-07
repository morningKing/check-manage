/**
 * FileUpload 控件单元测试
 *
 * 重点覆盖缺陷 04 #3：移除文件后删除项「复活」。
 *
 * 根因（修复前）：watch（FileUpload.vue:104-119）把 value 数组的 uid
 * （data_files 的 uuid 字符串）重映射为数组下标 0/1/…，而 handleRemove
 * （FileUpload.vue:192-196）按 f.uid !== String(file.uid) 过滤——uuid 与
 * 下标永不匹配 → emit 回去的数组原样未变 → 父组件写回 modelValue 后
 * watch 重渲染，删除项复活。
 *
 * 测试挂载真实 ElementPlus（el-upload 渲染上传列表），点击列表项的
 * 关闭图标走真实 on-remove 路径，断言 update:modelValue 收缩为 1 项；
 * 再把 emit 结果写回 modelValue 验证「复活」路径消失。
 */

import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ElementPlus from 'element-plus'

vi.mock('@/api/dataFiles', () => ({
  uploadDataFile: vi.fn(),
  authedDataFileUrl: vi.fn((url: string) => url),
}))

import FileUpload from '../FileUpload.vue'
import type { FieldConfig, UploadFile as UploadFileInfo } from '@/types'

const UID_A = '11111111-1111-4111-8111-111111111111'
const UID_B = '22222222-2222-4222-8222-222222222222'

const FILES: UploadFileInfo[] = [
  {
    uid: UID_A,
    name: 'dtest-甲.txt',
    url: `/api/data-files/${UID_A}/download`,
    size: 24,
    type: 'text/plain',
  },
  {
    uid: UID_B,
    name: 'dtest-乙.txt',
    url: `/api/data-files/${UID_B}/download`,
    size: 24,
    type: 'text/plain',
  },
]

function makeField(): FieldConfig {
  return {
    id: 'f1',
    label: '附件',
    fieldName: 'attach',
    controlType: 'file',
    required: false,
    order: 1,
  } as FieldConfig
}

async function mountWithFiles() {
  const wrapper = mount(FileUpload, {
    props: { field: makeField(), modelValue: [...FILES] },
    global: { plugins: [ElementPlus] },
  })
  await nextTick()
  return wrapper
}

describe('FileUpload', () => {
  it('modelValue 同步到上传列表（既有文件按原样渲染）', async () => {
    const wrapper = await mountWithFiles()
    const items = wrapper.findAll('.el-upload-list__item')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('dtest-甲.txt')
    expect(items[1].text()).toContain('dtest-乙.txt')
  })

  it('移除第一个文件后 update:modelValue 只剩第二个（04 #3 删除项复活回归）', async () => {
    const wrapper = await mountWithFiles()
    const items = wrapper.findAll('.el-upload-list__item')
    expect(items).toHaveLength(2)

    // 走真实 UI 路径：hover 出关闭图标 → 点击 → el-upload on-remove → handleRemove
    await items[0].find('.el-icon--close').trigger('click')
    await nextTick()

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted, '移除必须 emit update:modelValue').toBeTruthy()
    const last = emitted![emitted!.length - 1][0] as UploadFileInfo[]
    expect(last, '删除项不应残留在 emit 数组里').toHaveLength(1)
    expect(last[0].name).toBe('dtest-乙.txt')
    expect(last[0].uid).toBe(UID_B)

    // 复活路径回归：父组件把 emit 结果写回 modelValue → watch 重渲染，
    // 上传列表必须只剩 1 项（修复前该数组原样发回，删除项复活为 2 项）
    await wrapper.setProps({ modelValue: last })
    await nextTick()
    const itemsAfter = wrapper.findAll('.el-upload-list__item')
    expect(itemsAfter, '删除项复活').toHaveLength(1)
    expect(itemsAfter[0].text()).toContain('dtest-乙.txt')
  })

  it('移除后 uid 保留原始 uuid（重映射不再破坏标识，删除可重复操作）', async () => {
    const wrapper = await mountWithFiles()
    const items = wrapper.findAll('.el-upload-list__item')
    await items[1].find('.el-icon--close').trigger('click')
    await nextTick()
    const emitted = wrapper.emitted('update:modelValue')!
    const last = emitted[emitted.length - 1][0] as UploadFileInfo[]
    expect(last).toHaveLength(1)
    expect(last[0].uid).toBe(UID_A)
    expect(last[0].url).toBe(`/api/data-files/${UID_A}/download`)
  })
})
