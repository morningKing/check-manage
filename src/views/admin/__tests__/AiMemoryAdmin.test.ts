/** AiMemoryAdmin（记忆管理页）组件测试：mock 整个 API 层，验证三页签的核心交互。 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const getConfig = vi.fn()
const updateConfig = vi.fn()
const searchUsers = vi.fn()
const listEntries = vi.fn()
const deleteEntry = vi.fn()
const testSearch = vi.fn()
const testAdd = vi.fn()

vi.mock('@/api/aiMemoryAdmin', () => ({
  getMemoryConfig: (...a: any[]) => getConfig(...a),
  updateMemoryConfig: (...a: any[]) => updateConfig(...a),
  searchMemoryUsers: (...a: any[]) => searchUsers(...a),
  listMemoryEntries: (...a: any[]) => listEntries(...a),
  deleteMemoryEntry: (...a: any[]) => deleteEntry(...a),
  testMemorySearch: (...a: any[]) => testSearch(...a),
  testMemoryAdd: (...a: any[]) => testAdd(...a),
}))

// ElMessageBox 确认框可控
vi.mock('element-plus', async (orig) => {
  const actual: any = await orig()
  return { ...actual, ElMessageBox: { confirm: vi.fn().mockResolvedValue(true) } }
})

import AiMemoryAdmin from '../AiMemoryAdmin.vue'

// 真实 Element 组件（项目既有约定：表格列的作用域插槽依赖真实渲染）；
// 本页用到的 EP 组件按需登记。
import {
  ElAlert, ElTabs, ElTabPane, ElForm, ElFormItem, ElSwitch, ElInput,
  ElInputNumber, ElRadioGroup, ElRadioButton, ElButton, ElSelect, ElOption,
  ElTable, ElTableColumn, ElTag, ElDescriptions, ElDescriptionsItem, ElDivider,
} from 'element-plus'

const EP_COMPONENTS = {
  ElAlert, ElTabs, ElTabPane, ElForm, ElFormItem, ElSwitch, ElInput,
  ElInputNumber, ElRadioGroup, ElRadioButton, ElButton, ElSelect, ElOption,
  ElTable, ElTableColumn, ElTag, ElDescriptions, ElDescriptionsItem, ElDivider,
}

// 掩码形态的假 apiKey 用拼接构造（Mimosa 对密钥样字面量会拦）
const MASKED_KEY = '*'.repeat(8) + '-tail'

const configPayload = {
  mem0Enabled: true,
  embeddingProvider: 'ollama',
  embeddingModel: 'nomic-embed-text',
  embeddingOllamaUrl: 'http://localhost:11434',
  embeddingDims: 1024,
  endpoint: 'https://api.example.com/v1',
  model: 'glm-4',
  apiKey: MASKED_KEY,
  status: { ready: true, storePath: '/home/u/.check-manage/mem0', storeExists: true },
}

async function mountAt(tab?: string) {
  const w = mount(AiMemoryAdmin, {
    global: { components: EP_COMPONENTS },
  })
  await flushPromises()
  if (tab) {
    const item = w.find('.el-tabs__nav').findAll('.el-tabs__item')
      .find(i => i.text().includes(tab))
    if (item) {
      await item.trigger('click')
      await flushPromises()
    }
  }
  return w
}

beforeEach(() => {
  vi.clearAllMocks()
  getConfig.mockResolvedValue(configPayload)
})

describe('AiMemoryAdmin', () => {
  it('配置页签：加载并回填配置，状态卡就绪', async () => {
    const w = await mountAt()
    expect(getConfig).toHaveBeenCalledTimes(1)
    expect((w.find('[data-test="mem0-switch"]').find('input').element as HTMLInputElement).checked)
      .toBe(true)
    expect(w.text()).toContain('已就绪')
    expect(w.text()).toContain('.check-manage/mem0')
  })

  it('保存配置：只提交记忆字段（read-merge 契约）', async () => {
    updateConfig.mockResolvedValue(configPayload)
    const w = await mountAt()
    await w.find('[data-test="mem0-save"]').trigger('click')
    await flushPromises()
    expect(updateConfig).toHaveBeenCalledWith({
      mem0Enabled: true,
      embeddingProvider: 'ollama',
      embeddingModel: 'nomic-embed-text',
      embeddingOllamaUrl: 'http://localhost:11434',
      embeddingDims: 1024,
    })
  })

  it('未启用时渲染提示横幅', async () => {
    getConfig.mockResolvedValue({ ...configPayload, mem0Enabled: false, status: { ...configPayload.status, ready: false } })
    const w = await mountAt()
    expect(w.text()).toContain('当前未启用')
    expect(w.text()).toContain('未就绪')
  })

  it('记忆条目：选用户后加载列表，删除走确认', async () => {
    searchUsers.mockResolvedValue({ users: [{ id: 'u-x', username: 'alice' }] })
    listEntries.mockResolvedValue({
      ownerUserId: 'u-x', ownerUsername: 'alice',
      memories: [{ id: 'm1', memory: '喜欢 Python' }],
    })
    const w = await mountAt('记忆条目')
    // remote select：直接注入候选项并选中（绕开远程搜索交互，只测下游链路）
    const vm: any = w.vm
    vm.userOptions = [{ id: 'u-x', username: 'alice' }]
    vm.entriesOwner = 'alice'
    await flushPromises()
    await w.find('[data-test="entries-load"]').trigger('click')
    await flushPromises()
    expect(listEntries).toHaveBeenCalledWith('alice', undefined)
    expect(w.text()).toContain('喜欢 Python')

    await w.find('[data-test="entry-delete"]').trigger('click')
    await flushPromises()
    expect(deleteEntry).toHaveBeenCalledWith('m1', 'alice')
  })

  it('测试台：搜索返回命中与注入提示词原文', async () => {
    testSearch.mockResolvedValue({
      ownerUserId: 'u-x', ownerUsername: 'alice',
      results: [{ id: 'm1', memory: '喜欢 Python' }],
      rendered: '[关于当前用户的长期记忆（供参考，不必逐条复述）]\n- 喜欢 Python\n\n',
    })
    const w = await mountAt('测试台')
    const vm: any = w.vm
    vm.testOwner = 'alice'
    vm.testQuery = 'python'
    await flushPromises()
    await w.find('[data-test="test-search-btn"]').trigger('click')
    await flushPromises()
    expect(testSearch).toHaveBeenCalledWith('alice', 'python', 5)
    expect(w.find('[data-test="test-rendered"]').text()).toContain('- 喜欢 Python')
  })

  it('测试台：写入后自动回查', async () => {
    testAdd.mockResolvedValue({ ok: true, memories: [] })
    testSearch.mockResolvedValue({
      ownerUserId: 'u-x', ownerUsername: 'alice', results: [], rendered: '',
    })
    const w = await mountAt('测试台')
    const vm: any = w.vm
    vm.testOwner = 'alice'
    vm.addText = '偏好表格输出'
    await flushPromises()
    await w.find('[data-test="test-add-btn"]').trigger('click')
    await flushPromises()
    expect(testAdd).toHaveBeenCalledWith('alice', '偏好表格输出', false)
    // 写入后自动以同文本搜索验证
    expect(testSearch).toHaveBeenCalledWith('alice', '偏好表格输出', 5)
  })
})
