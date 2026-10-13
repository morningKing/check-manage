import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { ElTable, ElTableColumn, ElTag, ElAlert } from 'element-plus'
import AdminChildGate from '../AdminChildGate.vue'
import type { AdminChildGate as GateData, AdminGateExpectation } from '@/api/aiBatchAdmin'

const exp = (over: Partial<AdminGateExpectation>): AdminGateExpectation => ({
  name: '规则', mode: 'post', timing: 'before', checkType: 'tool',
  tool: 'read', argsPattern: 'a', minCount: 1, firedCount: 0, maxChecks: 1,
  lastStatus: null, lastEvidence: null, lastCheckedAt: null, scope: 'tree',
  ...over,
})

async function makeWrapper(props: any) {
  const w = mount(AdminChildGate, {
    props: { gate: null, loading: false, ...props },
    // 真实 Element 组件：表格列的作用域插槽依赖真实渲染（项目既有约定）
    global: { components: { ElTable, ElTableColumn, ElTag, ElAlert } },
  })
  await new Promise(r => setTimeout(r, 0))   // ElTable 行异步渲染
  return w
}

describe('AdminChildGate', () => {
  it('渲染终态徽标与三型期望清单：拦截/过程检查命中/终态核对', async () => {
    const gate: GateData = {
      gateStatus: 'passed', gateError: null,
      expectations: [
        exp({ name: '禁止回显探针', mode: 'pre', tool: 'bash', argsPattern: 'forbidden-probe' }),
        exp({ name: '读取前确认路径', mode: 'checkpoint', timing: 'before',
              firedCount: 1, maxChecks: 1 }),
        exp({ name: '读取输入文件', mode: 'post', lastStatus: 'passed', lastEvidence: 1 }),
      ],
    }
    const w = await makeWrapper({ gate })
    expect(w.find('[data-test="child-gate"]').exists()).toBe(true)
    expect(w.find('[data-test="gate-status"]').text()).toBe('已通过')
    expect(w.find('[data-test="gate-table"]').exists()).toBe(true)
    const text = w.text()
    expect(text).toContain('拦截（前置）')
    expect(text).toContain('过程检查（前）')
    expect(text).toContain('终态核对')
    expect(w.find('[data-test="gate-fired"]').text()).toBe('1 / 1')
    expect(text).toContain('已通过（证据 1）')
    // 触发条件可见（tool ~ /pattern/）
    expect(text).toContain('bash ~ /forbidden-probe/')
  })

  it('未核对状态与空期望文案', async () => {
    const w = await makeWrapper({ gate: { gateStatus: null, gateError: null, expectations: [] } })
    expect(w.find('[data-test="gate-status"]').text()).toBe('未核对')
    expect(w.text()).toContain('没有登记门禁期望')
  })

  it('加载中占位', async () => {
    const w = await makeWrapper({ loading: true })
    expect(w.text()).toContain('加载中')
    expect(w.find('[data-test="gate-table"]').exists()).toBe(false)
  })
})
