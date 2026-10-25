import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import McpToolGovernanceDrawer from '@/components/admin/McpToolGovernanceDrawer.vue'
import { getMcpTools, putMcpToolRules } from '@/api/aiMcpServers'

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

const FIXTURE = {
  groups: [
    { id: 'data-query', name: '数据查询', order: 1, tools: [
      { name: 'query_collection', description: '按条件查询集合', access: 'read' as const,
        rules: { admin: true, developer: true, guest: false, 'kefu-guest': true } },
      { name: 'export_collection_excel', description: '导出 Excel', access: 'read' as const,
        rules: { admin: true, developer: true, guest: false, 'kefu-guest': false } },
    ] },
    { id: 'exec', name: '通用执行与产物', order: 7, tools: [
      { name: 'run_python', description: '执行 Python', access: 'write' as const,
        rules: { admin: true, developer: true, guest: false, 'kefu-guest': false } },
    ] },
  ],
  roles: [
    { role: 'admin', label: '管理员' }, { role: 'developer', label: '开发' },
    { role: 'guest', label: '访客' }, { role: 'kefu-guest', label: '客服' },
  ],
}

describe('McpToolGovernanceDrawer', () => {
  beforeEach(() => {
    // 组件对返回对象原地改（无深拷贝），共享 FIXTURE 必须逐测试克隆隔离
    vi.mocked(getMcpTools).mockResolvedValue(structuredClone(FIXTURE))
    vi.mocked(putMcpToolRules).mockResolvedValue({ updated: 1, diff: 'x' })
  })

  it('按组渲染工具 + 角色列头 + 开关初值', async () => {
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true }, global: { stubs } })
    await flushPromises()
    const text = w.text()
    expect(text).toContain('数据查询')
    expect(text).toContain('通用执行与产物')
    expect(text).toContain('query_collection')
    expect(text).toContain('run_python')
    // 组批量开关（2 组×4 角色）+ 工具开关（3 工具×4 角色）= 20
    expect(w.findAll('input[type="checkbox"]').length).toBe(2 * 4 + 3 * 4)
  })

  it('切换开关 → 脏标记 → 保存发全量矩阵', async () => {
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true }, global: { stubs } })
    await flushPromises()
    // DOM 序：组1批量(0-3) 工具1行(4-7) 工具2行(8-11) 组2批量(12-15) run_python行(16-19)
    // 列序 = roles：admin, developer, guest, kefu-guest → run_python×developer = 17
    const checkboxes = w.findAll('input[type="checkbox"]')
    await checkboxes[17].setValue(false)
    await flushPromises()
    expect(w.text()).toContain('保存')
    const saveBtn = w.findAll('button').find(b => b.text().includes('保存'))!
    await saveBtn.trigger('click')
    await flushPromises()
    const payload = vi.mocked(putMcpToolRules).mock.calls[0][0]
    expect(payload.length).toBe(3 * 4)
    expect(payload).toContainEqual(
      { role: 'developer', tool_name: 'run_python', allowed: false })
  })

  it('空 groups 显示未同步提示', async () => {
    vi.mocked(getMcpTools).mockResolvedValue({ groups: [], roles: [] })
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true }, global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('未同步')
  })
})
