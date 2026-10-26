import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import McpToolGovernanceDrawer from '@/components/admin/McpToolGovernanceDrawer.vue'
import { getMcpTools, putMcpToolRules } from '@/api/aiMcpServers'
import type { McpGovernance } from '@/api/aiMcpServers'

vi.mock('@/api/aiMcpServers', () => ({
  getMcpTools: vi.fn(),
  putMcpToolRules: vi.fn(),
}))

// el-drawer 桩：绕开 teleport/lazy 渲染，slot 直出（照抄 MemoryManager.test.ts 模式）
const drawerStub = defineComponent({
  template: '<div><slot /></div>',
  props: { modelValue: { type: Boolean, default: false },
           title: { type: String, default: '' },
           size: { type: String, default: '' } },
  emits: ['update:modelValue'],
})

const stubs = {
  'el-drawer': drawerStub,
  // el-switch 桩：checkbox 直驱，change 事件带 checked 布尔（与组件 @change 契约一致）
  'el-switch': {
    template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'change\', $event.target.checked)" />',
    props: ['modelValue'], emits: ['change'],
  },
  'el-button': {
    template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
    props: ['disabled'], emits: ['click'],
  },
  'el-tag': { template: '<span class="stub-tag"><slot /></span>', props: ['type'] },
  'el-alert': { template: '<div class="stub-alert"><slot /><span>{{ title }}</span></div>', props: ['title'] },
}

const FIXTURE: McpGovernance = {
  groups: [
    { id: 'data-query', name: '数据查询', order: 1, tools: [
      { name: 'query_collection', description: '按条件查询集合', access: 'read',
        rules: { admin: true, developer: true, guest: false, 'kefu-guest': true } },
      { name: 'export_collection_excel', description: '导出 Excel', access: 'read',
        rules: { admin: true, developer: true, guest: false, 'kefu-guest': false } },
    ] },
    { id: 'exec', name: '通用执行与产物', order: 7, tools: [
      { name: 'run_python', description: '执行 Python', access: 'write',
        rules: { admin: true, developer: true, guest: false, 'kefu-guest': false } },
    ] },
  ],
  roles: [
    { role: 'admin', label: '管理员' }, { role: 'developer', label: '开发' },
    { role: 'guest', label: '访客' }, { role: 'kefu-guest', label: '客服' },
  ],
}

function cell(w: ReturnType<typeof mount>, tool: string, role: string) {
  return w.find(`[data-test="gov-cell"][data-tool="${tool}"][data-role="${role}"]`)
}

describe('McpToolGovernanceDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 组件对返回对象原地改（无深拷贝），共享 FIXTURE 必须逐测试克隆隔离
    vi.mocked(getMcpTools).mockResolvedValue(structuredClone(FIXTURE))
    vi.mocked(putMcpToolRules).mockResolvedValue({ updated: 1, diff: 'x' })
  })

  it('全局列头 + 分组渲染 + 角色列对齐（开关计数）', async () => {
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true }, global: { stubs } })
    await flushPromises()
    const text = w.text()
    expect(text).toContain('数据查询')
    expect(text).toContain('通用执行与产物')
    expect(text).toContain('query_collection')
    expect(text).toContain('run_python')
    // 全局列头只出现一次（不再每行重复角色标签）
    expect(w.findAll('.gov-head').length).toBe(1)
    // 组批量开关（2 组×4 角色，组头行内）+ 工具开关（3 工具×4 角色）= 20
    expect(w.findAll('input[type="checkbox"]').length).toBe(2 * 4 + 3 * 4)
    // 批量开关与角色列对齐：组头行里每个角色列恰一个开关
    const head = w.find('.gov-group__head')
    expect(head.findAll('.gov-col-role input[type="checkbox"]').length).toBe(4)
  })

  it('切换开关 → 变更高亮 + 改动计数 → 保存发全量矩阵且基线刷新', async () => {
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true }, global: { stubs } })
    await flushPromises()
    const devCell = cell(w, 'run_python', 'developer')
    await devCell.find('input[type="checkbox"]').setValue(false)
    await flushPromises()
    // 变更单元格高亮 + 保存栏计数与常驻
    expect(devCell.classes()).toContain('is-changed')
    expect(w.find('.gov-footer__dirty').classes()).toContain('on')
    expect(w.text()).toContain('有未保存改动（1 项）')
    const saveBtn = w.findAll('button').find(b => b.text() === '保存')!
    await saveBtn.trigger('click')
    await flushPromises()
    const payload = vi.mocked(putMcpToolRules).mock.calls[0][0]
    expect(payload.length).toBe(3 * 4)
    expect(payload).toContainEqual(
      { role: 'developer', tool_name: 'run_python', allowed: false })
    // 保存后基线刷新：高亮消失、状态回「与线上一致」
    expect(cell(w, 'run_python', 'developer').classes()).not.toContain('is-changed')
    expect(w.text()).toContain('与线上一致')
  })

  it('重置：撤销未保存改动回基线', async () => {
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true }, global: { stubs } })
    await flushPromises()
    await cell(w, 'run_python', 'developer').find('input[type="checkbox"]').setValue(false)
    await flushPromises()
    const resetBtn = w.findAll('button').find(b => b.text() === '重置')!
    await resetBtn.trigger('click')
    await flushPromises()
    const box = cell(w, 'run_python', 'developer')
      .find('input[type="checkbox"]').element as HTMLInputElement
    expect(box.checked).toBe(true)
    expect(w.text()).toContain('与线上一致')
    expect(vi.mocked(putMcpToolRules).mock.calls.length).toBe(0)
  })

  it('搜索过滤工具与分组', async () => {
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true }, global: { stubs } })
    await flushPromises()
    await w.find('.gov-search').setValue('run_python')
    await flushPromises()
    expect(w.find('[data-tool="run_python"]').exists()).toBe(true)
    expect(w.find('[data-tool="query_collection"]').exists()).toBe(false)
    expect(w.text()).toContain('1 / 3 个工具')
    // 清空恢复
    await w.find('.gov-search').setValue('不存在的关键词')
    await flushPromises()
    expect(w.text()).toContain('0 / 3 个工具')
    expect(w.text()).toContain('没有匹配')
  })

  it('组折叠：收起后工具行隐藏、批量开关仍在组头', async () => {
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true }, global: { stubs } })
    await flushPromises()
    await w.find('.gov-group__toggle').trigger('click')
    await flushPromises()
    expect(w.find('[data-tool="query_collection"]').exists()).toBe(false)
    expect(w.find('.gov-group__head').findAll('input[type="checkbox"]').length).toBe(4)
  })

  it('空 groups 显示未同步提示', async () => {
    vi.mocked(getMcpTools).mockResolvedValue({ groups: [], roles: [] })
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true }, global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('未同步')
  })
})
