/**
 * PluginToggleTable 组件测试（plugin-architecture spec §7）。
 *
 * 真实 Element Plus 挂载惯例；@/api/plugins 整模块 mock、@/stores/plugins
 * stub（refresh spy）。断言：行渲染/开关乐观翻转与参数/失败回滚+错误提示/
 * 成功后 refresh 同步其它入口的守卫状态。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ElSwitch, ElTable, ElTableColumn, ElAlert } from 'element-plus'
import PluginToggleTable from '../PluginToggleTable.vue'

const getPlugins = vi.hoisted(() => vi.fn())
const setPluginEnabled = vi.hoisted(() => vi.fn())
vi.mock('@/api/plugins', () => ({
  getPlugins: getPlugins,
  setPluginEnabled: setPluginEnabled,
}))
const refresh = vi.hoisted(() => vi.fn())
vi.mock('@/stores/plugins', () => ({
  usePluginsStore: () => ({ refresh }),
}))
vi.mock('element-plus', async (importOriginal) => {
  const orig: Record<string, unknown> = await importOriginal()
  return { ...orig, ElMessage: { error: ElMessageErrorSpy, success: vi.fn() } }
})
const ElMessageErrorSpy = vi.hoisted(() => vi.fn())

const LIST = [
  { id: 'smart-writing', name: '智能写作', description: '协作式智能文档编辑', version: '1.0.0', enabled: true },
  { id: 'another', name: '另一个插件', description: '描述', version: '0.2.0', enabled: false },
]

beforeEach(() => {
  vi.clearAllMocks()
  // 深拷贝：组件会乐观突变行对象——共享 LIST 会让「成功翻转」泄漏进后续
  // 用例的初值（test3 回滚断言曾因此假失败）
  getPlugins.mockResolvedValue(LIST.map((p) => ({ ...p })))
})

async function mountTable() {
  const w = mount(PluginToggleTable, {
    global: {
      components: { ElTable, ElTableColumn, ElAlert, ElSwitch },
    },
  })
  await flushPromises()
  return w
}

describe('PluginToggleTable', () => {
  it('渲染插件行：名称/描述/版本/开关初值', async () => {
    const w = await mountTable()
    const rows = w.findAll('.el-table__row')
    expect(rows).toHaveLength(2)
    expect(w.text()).toContain('智能写作')
    expect(w.text()).toContain('协作式智能文档编辑')
    expect(w.text()).toContain('1.0.0')
    const switches = w.findAllComponents(ElSwitch)
    expect((switches[0].props('modelValue'))).toBe(true)
    expect((switches[1].props('modelValue'))).toBe(false)
  })

  it('关闭开关：乐观翻转 + setPluginEnabled(id,false) + 成功后 refresh()', async () => {
    setPluginEnabled.mockResolvedValue({ id: 'smart-writing', enabled: false })
    const w = await mountTable()
    const sw = w.findAllComponents(ElSwitch)[0]
    await sw.vm.$emit('update:modelValue', false)
    await sw.vm.$emit('change', false)
    await flushPromises()
    expect(setPluginEnabled).toHaveBeenCalledWith('smart-writing', false)
    expect(w.findAllComponents(ElSwitch)[0].props('modelValue')).toBe(false)
    expect(refresh).toHaveBeenCalled()
    expect(ElMessageErrorSpy).not.toHaveBeenCalled()
  })

  it('请求失败：开关回滚 + 错误提示 + 不 refresh', async () => {
    setPluginEnabled.mockRejectedValue({
      response: { data: { error: '权限不足' } },
    })
    const w = await mountTable()
    const sw = w.findAllComponents(ElSwitch)[0]
    await sw.vm.$emit('update:modelValue', false)
    await sw.vm.$emit('change', false)
    await flushPromises()
    expect(w.findAllComponents(ElSwitch)[0].props('modelValue')).toBe(true)
    expect(ElMessageErrorSpy).toHaveBeenCalled()
    expect(refresh).not.toHaveBeenCalled()
  })

  it('加载失败：空态提示不崩', async () => {
    getPlugins.mockRejectedValue(new Error('net'))
    const w = await mountTable()
    await flushPromises()
    expect(w.text()).toBeTruthy()   // 组件存活
    expect(w.findAllComponents(ElSwitch)).toHaveLength(0)
  })
})
