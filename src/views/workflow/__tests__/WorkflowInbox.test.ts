/**
 * WorkflowInbox 渲染测试
 *
 * mock 工作流 store 返回 2 条待办，断言渲染 2 行 + "去处理" 操作存在。
 */
import { describe, it, expect, beforeAll, vi } from 'vitest'
import { ref } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import type { WorkflowInboxItem } from '@/types/workflow'

// Polyfill ResizeObserver for jsdom (Element Plus table)
beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

const inboxItems: WorkflowInboxItem[] = [
  {
    instanceId: 'i1',
    workflowName: '采购审批',
    stageName: '部门审核',
    collection: 'orders',
    recordId: 'r1',
    enteredAt: '2026-06-01T08:00:00Z',
  },
  {
    instanceId: 'i2',
    workflowName: '请假流程',
    stageName: '经理审批',
    collection: 'leaves',
    recordId: 'r2',
    enteredAt: '2026-06-02T09:30:00Z',
  },
]

// mock 路由
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

// mock workflow store: 返回真实 ref，storeToRefs 可正常解包
const loadInbox = vi.fn()
const inboxRef = ref<WorkflowInboxItem[]>(inboxItems)
const loadingRef = ref(false)
vi.mock('@/stores/workflow', () => ({
  useWorkflowStore: () => ({
    inbox: inboxRef,
    loading: loadingRef,
    loadInbox,
  }),
}))

// storeToRefs 直接透传 store 上的 ref
vi.mock('pinia', () => ({
  storeToRefs: (store: any) => ({ inbox: store.inbox, loading: store.loading }),
}))

vi.mock('@/stores/menu', () => ({
  useMenuStore: () => ({ menuList: [] }),
}))

vi.mock('@/stores/jumpNavigation', () => ({
  useJumpNavigationStore: () => ({ setJump: vi.fn() }),
}))

vi.mock('@/utils/request', () => ({ post: vi.fn().mockResolvedValue({}) }))

import WorkflowInbox from '../WorkflowInbox.vue'

const stubs = {
  // 每行渲染一次默认插槽，并通过 provide 把 row 传给列
  'el-table': {
    template: '<div class="el-table"><div v-for="(row, i) in data" :key="i" class="el-table__row"><row-provider :row="row"><slot /></row-provider></div></div>',
    props: ['data'],
    components: {
      'row-provider': {
        template: '<div><slot /></div>',
        props: ['row'],
        provide(this: any) {
          return { tableRow: this.row }
        },
      },
    },
  },
  'el-table-column': {
    template: '<div class="el-table-column"><slot :row="tableRow" /></div>',
    inject: { tableRow: { default: {} } },
  },
  'el-button': {
    template: '<button @click="$emit(\'click\')"><slot /></button>',
    emits: ['click'],
  },
  'el-icon': { template: '<i><slot /></i>' },
}

describe('WorkflowInbox', () => {
  it('renders one row per inbox item with a 去处理 action', async () => {
    const wrapper = mount(WorkflowInbox, {
      global: {
        stubs,
        directives: { loading: {} },
      },
    })
    await flushPromises()

    expect(loadInbox).toHaveBeenCalled()

    // 操作列每行渲染一个"去处理"按钮
    const goButtons = wrapper
      .findAll('button')
      .filter((b) => b.text().includes('去处理'))
    expect(goButtons.length).toBe(2)
  })
})

// P2 Phase B：AI 审批项投影到收件箱（kind='ai_approval'）→ 渲染类型标签 + 通过/拒绝
describe('WorkflowInbox with AI approvals', () => {
  it('renders ai_approval kind tag with approve/reject actions', async () => {
    inboxRef.value = [
      {
        kind: 'ai_approval',
        approvalId: 'apr-1',
        instanceId: 'run-1',
        workflowName: 'AI 编排审批',
        stageName: '发布前审批',
        collection: 'ai_orchestration_runs',
        recordId: 'run-1',
        enteredAt: '2026-09-23T10:00:00Z',
      },
    ] as any
    const wrapper = mount(WorkflowInbox, {
      global: { stubs, directives: { loading: {} } },
    })
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('AI 审批')
    expect(text).toContain('通过')
    expect(text).toContain('拒绝')
    wrapper.unmount()
    inboxRef.value = inboxItems
  })
})
